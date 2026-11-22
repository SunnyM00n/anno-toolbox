"""The sound editor's waveform view: the peaks of a decoded clip, the codecs it reads, the window drawing it."""

from __future__ import annotations

import array
import math

import pytest
from tests.formats.formatfakes import NEW_BANK as NEW
from tests.soundedit.soundeditfakes import bank_window

from annotoolbox.formats import bnk
from annotoolbox.soundedit import clipcodec, waveform


def sine(n: int = 640, amplitude: int = 8000) -> array.array:
    return array.array("h", [round(amplitude * math.sin(i / 9)) for i in range(n)])


def test_the_peaks_are_the_lowest_and_highest_sample_of_each_column():
    clip = clipcodec.SClip(array.array("h", [0, 5, -3, 9, 2, -7, 1, 1]), 8000, 1)
    assert waveform.peaks(clip, 4) == ((0, 5), (-3, 9), (-7, 2), (1, 1))
    assert waveform.peaks(clipcodec.SClip(array.array("h"), 8000, 1), 3) == ((0, 0),) * 3
    assert len(waveform.peaks(clip, 100)) == 100  # more columns than samples: the extra ones are flat


def test_a_pcm_and_an_adpcm_clip_are_drawn_with_their_length():
    clip = clipcodec.SClip(sine(6400), 32000, 1)
    for codec in ("pcm", "adpcm"):
        shape = waveform.shape(clipcodec.encode(clip, codec, NEW), 50)
        assert (shape.rate, shape.channels, len(shape.peaks)) == (32000, 1, 50), codec
        assert shape.seconds == pytest.approx(0.2, abs=0.005), codec
        assert max(hi for _lo, hi in shape.peaks) == pytest.approx(8000, rel=0.1), codec


def test_a_stereo_clip_is_drawn_from_both_channels():
    stereo = clipcodec.SClip(array.array("h", [100, -9000] * 64), 44100, 2)
    shape = waveform.shape(clipcodec.encode(stereo, "pcm", NEW), 4)
    assert shape.channels == 2 and shape.peaks[0] == (-9000, 100)


def test_a_clip_that_cannot_be_drawn_says_why(monkeypatch):
    with pytest.raises(clipcodec.CClipError):
        waveform.shape(b"not a wem", 10)
    monkeypatch.setattr(waveform, "MAX_BYTES", 10)
    with pytest.raises(clipcodec.CClipError, match="too large"):
        waveform.shape(bnk.pcm_wem(sine(64), 8000, 1), 10)


def test_a_vorbis_clip_is_drawn_when_the_tools_are_there():
    if clipcodec.availability(NEW)["vorbis"]:
        pytest.skip("needs ffmpeg and the codebook file")
    clip = clipcodec.SClip(sine(48000, 12000), 48000, 1)
    shape = waveform.shape(clipcodec.encode(clip, "vorbis", NEW), 20)
    assert shape.seconds == pytest.approx(1.0, abs=0.05) and max(hi for _lo, hi in shape.peaks) > 6000


def test_the_window_draws_the_picked_sound_and_says_when_there_is_none(sound_window, monkeypatch):
    window = bank_window(sound_window, monkeypatch, NEW)
    window.m_outline.expandAll()
    window.m_outline.setCurrentItem(window.m_outline.topLevelItem(1).child(0).child(0))  # sound 11, audio in the bank
    assert window.m_wave.m_shape is not None, window.m_wave.message
    assert window.m_wave.caption().endswith("1 channels")
    window.m_outline.setCurrentItem(window.m_outline.topLevelItem(0).child(0))  # an event: no clip
    assert window.m_wave.m_shape is None and window.m_wave.m_message
    window.m_wave.resize(300, 120)
    assert not window.m_wave.grab().isNull()  # paints either way
