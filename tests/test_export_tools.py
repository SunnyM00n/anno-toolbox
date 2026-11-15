"""The export through the real optional tools: ffmpeg (clips and movies made here), the Wwise Vorbis codebook file and,
on an install (``ANNO117_DIR``, ``ANNO2070_DIR``), a real Vorbis sound and a real Bink 1 movie. Each test skips when
its tool or install is missing; the error paths and the stand-in tool are in ``test_export_media.py``."""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from annotoolbox.exporting import banks, run
from annotoolbox.formats import riff, wwvorbis
from annotoolbox.infra import tools
from fakes import installed
from test_export_core import folder

needs_ffmpeg = pytest.mark.skipif(tools.find_tool("ffmpeg") is None, reason="needs ffmpeg")

RATE, SECONDS = 22050, 1


def sine_wav() -> bytes:
    n = RATE * SECONDS
    return riff.wav_bytes(struct.pack(f"<{n}h", *(int(8000 * math.sin(i * 0.05)) for i in range(n))), RATE, 1)


def export_one(tmp_path, files, path, formats):
    (entry,) = run.export_path(folder(tmp_path, files), path, formats, tmp_path / "out")
    return entry, tmp_path / "out"


def ogg_of_sine() -> bytes:
    """An Ogg Vorbis clip made by ffmpeg; the test skips when this ffmpeg has no Vorbis encoder."""
    try:
        return tools.ffmpeg_bytes(sine_wav(), ["-c:a", "libvorbis"], in_suffix=".wav", out_suffix=".ogg")
    except tools.ToolError as exc:
        pytest.skip(f"this ffmpeg makes no Ogg Vorbis: {exc}")


@needs_ffmpeg
def test_a_wav_becomes_ogg_vorbis_and_back_through_the_real_ffmpeg(tmp_path):
    entry, out = export_one(tmp_path, {"s/a.wav": sine_wav()}, "s/a.wav", {"ogg"})
    if entry.status == "failed" and "libvorbis" in " ".join(entry.notes):
        pytest.skip("this ffmpeg makes no Ogg Vorbis")
    assert entry.status == "written", entry.notes
    ogg = (out / "s/a.ogg").read_bytes()
    assert ogg[:4] == b"OggS" and b"vorbis" in ogg[:64]
    back, back_out = export_one(tmp_path / "again", {"s/a.ogg": ogg}, "s/a.ogg", {"wav"})
    assert back.status == "written", back.notes
    with wave.open(str(back_out / "s/a.wav")) as w:
        assert w.getframerate() == RATE and abs(w.getnframes() - RATE * SECONDS) < RATE // 10


@needs_ffmpeg
def test_an_ogg_file_becomes_a_wav_of_its_length(tmp_path):
    entry, out = export_one(tmp_path, {"s/b.ogg": ogg_of_sine()}, "s/b.ogg", {"wav"})
    assert entry.status == "written", entry.notes
    with wave.open(io.BytesIO((out / "s/b.wav").read_bytes())) as w:
        assert w.getnchannels() == 1 and abs(w.getnframes() - RATE * SECONDS) < RATE // 10


def clip(tmp_path, name: str, codec: list[str]):
    """A one-second 64 x 48 test movie made by ffmpeg itself, or a skip when it cannot make this codec."""
    target = tmp_path / name
    result = tools.run_tool(
        tools.require_tool("ffmpeg"),
        ["-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=63x47:rate=10", "-t", "1", *codec, str(target)],
        timeout=120,
    )
    if not result.ok or not target.is_file():
        pytest.skip(f"this ffmpeg cannot make {name}: {result.stderr.strip()[-80:]}")
    return target.read_bytes()


@needs_ffmpeg
def test_a_movie_becomes_mp4_and_webm_through_the_real_ffmpeg(tmp_path):
    entry, out = export_one(tmp_path, {"m/a.avi": clip(tmp_path, "a.avi", ["-c:v", "mpeg4"])}, "m/a.avi", {"mp4"})
    if entry.status == "failed" and "libx264" in " ".join(entry.notes):
        pytest.skip("this ffmpeg has no libx264")
    assert entry.status == "written", entry.notes
    assert (out / "m/a.mp4").read_bytes()[4:8] == b"ftyp"  # an odd size was padded to even, as the form says
    webm, webm_out = export_one(tmp_path / "web", {"m/a.avi": (out.parent / "a.avi").read_bytes()}, "m/a.avi", {"webm"})
    if webm.status == "failed" and "libvpx" in " ".join(webm.notes):
        pytest.skip("this ffmpeg has no VP9 encoder")
    assert webm.status == "written", webm.notes
    assert (webm_out / "m/a.webm").read_bytes()[:4] == b"\x1a\x45\xdf\xa3"


@pytest.mark.skipif(tools.find_tool("wwcodebooks") is None, reason="needs the Wwise codebook file")
@pytest.mark.skipif(installed("anno117") is None, reason="needs an Anno 117 install (ANNO117_DIR)")
def test_a_real_wwise_vorbis_sound_becomes_ogg_and_wav(tmp_path):
    game = installed("anno117")
    found = None
    for path in game.iter_paths("data/sound/generatedsoundbanks/windows/media/"):
        if path.endswith(".wem") and banks.wave_info(data := game.read(path))["format"] == "Wwise Vorbis":
            found = path, data
            break
    assert found, "no Wwise Vorbis sound among the install's streamed media"
    path, data = found
    assert wwvorbis.Library.find() is not None
    entry, out = export_one(tmp_path, {path: data}, path, {"ogg"})
    assert entry.status == "written", entry.notes
    ogg = (out / path.replace(".wem", ".ogg")).read_bytes()
    assert ogg[:4] == b"OggS" and b"vorbis" in ogg[:64]
    if tools.find_tool("ffmpeg") is None:
        return
    wav_entry, wav_out = export_one(tmp_path / "wav", {path: data}, path, {"wav"})
    assert wav_entry.status == "written", wav_entry.notes
    with wave.open(str(wav_out / path.replace(".wem", ".wav"))) as w:
        assert w.getnframes() > 0 and w.getframerate() in (44100, 48000, 24000, 22050, 32000)


@needs_ffmpeg
@pytest.mark.skipif(installed("anno2070") is None, reason="needs an Anno 2070 install (ANNO2070_DIR)")
def test_a_real_bink_1_movie_becomes_an_mp4(tmp_path):
    game = installed("anno2070")
    movies = [p for p in game.iter_paths("") if p.lower().endswith(".bik")]
    assert movies, "no .bik in the install"
    path = min(movies[:6], key=lambda p: len(game.read_bytes(p)))
    entry, out = export_one(tmp_path, {path: game.read_bytes(path)}, path, {"mp4"})
    assert entry.status == "written", entry.notes
    mp4 = (out / path.replace(".bik", ".mp4")).read_bytes()
    assert mp4[4:8] == b"ftyp" and len(mp4) > 1000
