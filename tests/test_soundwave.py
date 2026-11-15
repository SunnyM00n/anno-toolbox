"""The sound editor's waveform view: the peaks of a decoded clip, the codecs it reads, the window drawing it."""

from __future__ import annotations

import array
import math

import pytest
from tests.test_soundedit import NEW, bank_bytes

from annotoolbox.formats import bnk
from annotoolbox.soundedit import clipcodec, waveform
from annotoolbox.sources import gametree


def sine(n: int = 640, amplitude: int = 8000) -> array.array:
    return array.array("h", [round(amplitude * math.sin(i / 9)) for i in range(n)])


def test_the_peaks_are_the_lowest_and_highest_sample_of_each_column():
    clip = clipcodec.Clip(array.array("h", [0, 5, -3, 9, 2, -7, 1, 1]), 8000, 1)
    assert waveform.peaks(clip, 4) == ((0, 5), (-3, 9), (-7, 2), (1, 1))
    assert waveform.peaks(clipcodec.Clip(array.array("h"), 8000, 1), 3) == ((0, 0),) * 3
    assert len(waveform.peaks(clip, 100)) == 100  # more columns than samples: the extra ones are flat


def test_a_pcm_and_an_adpcm_clip_are_drawn_with_their_length():
    clip = clipcodec.Clip(sine(6400), 32000, 1)
    for codec in ("pcm", "adpcm"):
        shape = waveform.shape(clipcodec.encode(clip, codec, NEW), 50)
        assert (shape.rate, shape.channels, len(shape.peaks)) == (32000, 1, 50), codec
        assert shape.seconds == pytest.approx(0.2, abs=0.005), codec
        assert max(hi for _lo, hi in shape.peaks) == pytest.approx(8000, rel=0.1), codec


def test_a_stereo_clip_is_drawn_from_both_channels():
    stereo = clipcodec.Clip(array.array("h", [100, -9000] * 64), 44100, 2)
    shape = waveform.shape(clipcodec.encode(stereo, "pcm", NEW), 4)
    assert shape.channels == 2 and shape.peaks[0] == (-9000, 100)


def test_a_clip_that_cannot_be_drawn_says_why(monkeypatch):
    with pytest.raises(clipcodec.ClipError):
        waveform.shape(b"not a wem", 10)
    monkeypatch.setattr(waveform, "MAX_BYTES", 10)
    with pytest.raises(clipcodec.ClipError, match="too large"):
        waveform.shape(bnk.pcm_wem(sine(64), 8000, 1), 10)


def test_a_vorbis_clip_is_drawn_when_the_tools_are_there():
    if clipcodec.availability(NEW)["vorbis"]:
        pytest.skip("needs ffmpeg and the codebook file")
    clip = clipcodec.Clip(sine(48000, 12000), 48000, 1)
    shape = waveform.shape(clipcodec.encode(clip, "vorbis", NEW), 20)
    assert shape.seconds == pytest.approx(1.0, abs=0.05) and max(hi for _lo, hi in shape.peaks) > 6000


def test_the_window_draws_the_picked_sound_and_says_when_there_is_none(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.soundedit.soundwindow import SoundWindow
    from annotoolbox.suite.settings import Settings

    QApplication.instance() or QApplication([])
    window = SoundWindow(Settings())
    path = "data/sound/generatedsoundbanks/windows/9.bnk"
    files = {path: bank_bytes(NEW)}
    window.tree, window.key = gametree.GameTree("t", lambda p: files[p], files, lambda p: "t"), "anno117"
    window.open_bank(path)
    window.outline.expandAll()
    window.outline.setCurrentItem(window.outline.topLevelItem(1).child(0).child(0))  # sound 11, audio in the bank
    assert window.wave.shape is not None, window.wave.message
    assert window.wave.caption().endswith("1 channels")
    window.outline.setCurrentItem(window.outline.topLevelItem(0).child(0))  # an event: no clip
    assert window.wave.shape is None and window.wave.message
    window.wave.resize(300, 120)
    assert not window.wave.grab().isNull()  # paints either way
