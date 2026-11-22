"""Textures, pictures, sounds and sound banks exported as PNG, EXR, Radiance HDR, WAV; the writers' files read back."""

from __future__ import annotations

import struct
import wave
import zlib
from pathlib import Path

import pytest
from tests.ddsfiles import make_dxt1_dds
from tests.exporting.exportingfakes import NODE_BASE, adpcm_wem, bank_with_media, folder, wem_pcm

from annotoolbox.exporting import run
from annotoolbox.formats import bnk, imagewrite
from annotoolbox.infra import tools

np = pytest.importorskip("numpy")


def dds(width, height, fourcc: bytes, body: bytes, mips: int = 1, caps2: int = 0) -> bytes:
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | (0x20000 if mips > 1 else 0)
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height, width, 0, 0, mips) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, fourcc, 0, 0, 0, 0, 0) + struct.pack("<5I", 0x1000, caps2, 0, 0, 0)
    return head + body


def png_pixels(png: bytes) -> tuple[int, int, int, int, bytes]:
    """(width, height, depth, colour type, unfiltered rows) of a PNG whose rows all use filter 0."""
    width, height, depth, colour = struct.unpack_from(">IIBB", png, 16)
    at, idat = 8, b""
    while at < len(png):
        size, kind = struct.unpack_from(">I4s", png, at)
        assert zlib.crc32(png[at + 4 : at + 8 + size]) == struct.unpack_from(">I", png, at + 8 + size)[0]
        idat += png[at + 8 : at + 8 + size] if kind == b"IDAT" else b""
        at += 12 + size
    raw = zlib.decompress(idat)
    row = len(raw) // height
    return width, height, depth, colour, b"".join(raw[y * row + 1 : (y + 1) * row] for y in range(height))


def exr_channels(exr: bytes) -> tuple[list[str], int, int]:
    assert exr[:4] == b"v/1\x01"
    at, names = 8, []
    while exr[at] != 0:
        end = exr.index(b"\0", at)
        name, kind_end = exr[at:end].decode(), exr.index(b"\0", end + 1)
        size = struct.unpack_from("<I", exr, kind_end + 1)[0]
        body = exr[kind_end + 5 : kind_end + 5 + size]
        if name == "channels":
            p = 0
            while body[p] != 0:
                q = body.index(b"\0", p)
                names.append(body[p:q].decode())
                p = q + 1 + 16
        if name == "dataWindow":
            _x0, _y0, x1, y1 = struct.unpack("<4i", body)
        at = kind_end + 5 + size
    return names, x1 + 1, y1 + 1


def test_a_dxt1_texture_becomes_an_8_bit_png_with_its_decoded_texels(tmp_path):
    source = folder(tmp_path, {"t/red.dds": make_dxt1_dds(8, 4)})
    (entry,) = run.export_path(source, "t/red.dds", {"png"}, tmp_path / "out")
    assert entry.status == "written" and entry.files == ["t/red.dds", "t/red.png"]
    width, height, depth, colour, rows = png_pixels((tmp_path / "out/t/red.png").read_bytes())
    assert (width, height, depth, colour) == (8, 4, 8, 6) and rows[:4] == bytes([255, 0, 0, 255])


def test_every_mip_is_written_when_asked_and_the_16_bit_form_widens_the_bytes(tmp_path):
    body = bytes(8) * (1 + 1 + 1)  # 8x8 BC1: 4 blocks, 4x4: 1, 2x2: 1 (each at least one block)
    data = dds(8, 8, b"DXT1", bytes(8) * 4 + bytes(8) + bytes(8), mips=3)
    source = folder(tmp_path, {"t/a.dds": data})
    out = tmp_path / "out"
    (entry,) = run.export_path(source, "t/a.dds", {"png16"}, out, run.SOptions(all_mips=True))
    assert sorted(entry.files) == ["t/a.dds", "t/a_mip0.png", "t/a_mip1.png", "t/a_mip2.png"] and body
    _w, _h, depth, colour, rows = png_pixels((out / "t/a_mip0.png").read_bytes())
    assert (depth, colour) == (16, 6) and len(rows) == 8 * 8 * 8
    (plain,) = run.export_path(source, "t/a.dds", {"png"}, tmp_path / "o2", run.SOptions(png_depth=16))
    assert "t/a.png" in plain.files and any("png_depth" in n for n in plain.notes)


def test_a_cube_exports_each_face_and_a_float_texture_the_exr_and_hdr_forms(tmp_path):
    cube = dds(4, 4, b"DXT1", bytes(8) * 6, caps2=0x200 | 0xFC00)
    source = folder(tmp_path, {"t/sky.dds": cube})
    (entry,) = run.export_path(source, "t/sky.dds", {"png"}, tmp_path / "out")
    assert sorted(f for f in entry.files if f.endswith(".png")) == [f"t/sky_face{k}.png" for k in range(6)]
    (one,) = run.export_path(source, "t/sky.dds", {"png"}, tmp_path / "out1", run.SOptions(all_faces=False))
    assert [f for f in one.files if f.endswith(".png")] == ["t/sky.png"] and any(
        "only the first" in n for n in one.notes
    )

    values = np.array([[[1.5, 0.5, 0.25, 1.0], [0.0, 0.0, 0.0, 1.0]]] * 2, "<f4")  # 2 x 2 RGBA32F
    floats = dds(2, 2, bytes([116, 0, 0, 0]), values.tobytes())
    source = folder(tmp_path, {"t/sun.dds": floats})
    out = tmp_path / "hdr"
    (entry,) = run.export_path(
        source, "t/sun.dds", {"exr", "hdr", "png"}, out, run.SOptions(hdr_form="hdr", exposure=-1.0)
    )
    assert "t/sun.hdr" in entry.files and "t/sun.exr" not in entry.files and any("hdr_form" in n for n in entry.notes)
    assert (out / "t/sun.hdr").read_bytes().startswith(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 2 +X 2\n")
    (entry,) = run.export_path(source, "t/sun.dds", {"exr"}, tmp_path / "exr")
    names, width, height = exr_channels((tmp_path / "exr/t/sun.exr").read_bytes())
    assert (names, width, height) == (["A", "B", "G", "R"], 2, 2)


def test_the_writers_files_hold_what_was_given():
    assert imagewrite.png_bytes(2, 1, bytes([1, 2, 3, 4, 5, 6, 7, 8])) == imagewrite.png_bytes(2, 1, bytes(range(1, 9)))
    width, height, depth, colour, rows = png_pixels(imagewrite.png_bytes(2, 1, struct.pack("<2H", 258, 65535), 1, 16))
    assert (width, height, depth, colour) == (2, 1, 16, 0) and rows == struct.pack(">2H", 258, 65535)
    exr = imagewrite.exr_bytes(2, 1, {"Y": struct.pack("<2f", 1.0, 2.0)})
    assert exr_channels(exr) == (["Y"], 2, 1) and exr.endswith(struct.pack("<2f", 1.0, 2.0))
    assert imagewrite.rgbe(1.0, 0.5, 0.25) == bytes([128, 64, 32, 129])
    with pytest.raises(imagewrite.CImageError):
        imagewrite.png_bytes(2, 2, b"short")


def test_a_tga_becomes_a_png_and_a_png_stays_what_it_is(tmp_path):
    tga = bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 0, 1, 0, 24, 0]) + bytes(
        [0, 0, 255, 0, 255, 0]
    )  # bottom-up 2x1
    png = imagewrite.png_bytes(1, 1, bytes(4))
    source = folder(tmp_path, {"p/a.tga": tga, "p/b.png": png})
    out = tmp_path / "out"
    (entry,) = run.export_path(source, "p/a.tga", {"png"}, out)
    assert entry.files == ["p/a.tga", "p/a.png"]
    assert png_pixels((out / "p/a.png").read_bytes())[4] == bytes([255, 0, 0, 255, 0, 255, 0, 255])
    (kept,) = run.export_path(source, "p/b.png", {"png"}, out)
    assert kept.files == ["p/b.png"] and "already a PNG" in kept.notes[0]


def test_a_wem_becomes_a_wav_through_the_pcm_and_adpcm_readers(tmp_path):
    source = folder(tmp_path, {"s/a.wem": wem_pcm([0, 100, -100, 5]), "s/b.wem": adpcm_wem(bytes([0x70]))})
    out = tmp_path / "out"
    for name in ("s/a.wem", "s/b.wem"):
        (entry,) = run.export_path(source, name, {"wav"}, out)
        assert entry.status == "written" and entry.files[1] == name.replace(".wem", ".wav")
    with wave.open(str(out / "s/a.wav")) as w:
        assert (w.getnchannels(), w.getframerate(), w.readframes(4)) == (1, 22050, struct.pack("<4h", 0, 100, -100, 5))
    with wave.open(str(out / "s/b.wav")) as w:
        assert (w.getframerate(), w.getnframes()) == (48000, 64)


def test_an_ogg_form_without_its_tools_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "find_tool", lambda name: None)
    source = folder(tmp_path, {"s/a.wem": wem_pcm([1, 2, 3]), "s/b.mp3": b"ID3" + bytes(20)})
    (entry,) = run.export_path(source, "s/a.wem", {"ogg"}, tmp_path / "out")
    assert entry.status == "tool-missing" and "ffmpeg" in entry.notes[0]
    (mp3,) = run.export_path(source, "s/b.mp3", {"wav", "ogg"}, tmp_path / "out")
    assert mp3.status == "tool-missing" and mp3.files == ["s/b.mp3"]
    assert "codebook" in run.missing_tools({"ogg"})


def test_a_bank_exports_its_embedded_sounds_by_name_and_per_event_with_a_listing(tmp_path):
    info = '{"SoundBanksInfo": {"SoundBanks": [{"ShortName": "fx", "Media": [{"Id": 55, "ShortName": "boom", "Path": "a.wem"}], "Events": [{"Id": 1, "Name": "Play_Boom"}]}]}}'
    source = folder(tmp_path, {"snd/windows/fx.bnk": bank_with_media(), "snd/windows/fx.json": info.encode()})
    out = tmp_path / "out"
    (entry,) = run.export_path(source, "snd/windows/fx.bnk", {"wav", "json"}, out)
    assert entry.status == "written", entry.notes
    got = set(entry.files)
    assert {"snd/windows/fx/media/boom.wav", "snd/windows/fx/media/boom.wem", "snd/windows/fx/media/66.wav"} <= got
    assert "snd/windows/fx/events/Play_Boom/00_boom.wav" in got and "snd/windows/fx.json" in got
    with wave.open(str(out / "snd/windows/fx/media/66.wav")) as w:
        assert w.readframes(4) == struct.pack("<4h", 9, 8, 7, 6)
    listing = (out / "snd/windows/fx.json").read_text(encoding="utf-8")
    assert '"Play_Boom"' in listing and '"boom"' in listing


def test_a_streamed_sound_is_read_from_beside_the_bank(tmp_path):
    from annotoolbox.formats import bnksfx

    streamed = bnk.SHircObject(2, 11, struct.pack("<IBI", bnk.PCM_PLUGIN, 1, 77) + bytes(9) + NODE_BASE)
    bank = bnk.SSoundBank([bnk.SChunk(b"BKHD", struct.pack("<III", 172, 9, 0)), bnk.SChunk(b"HIRC", b"")])
    bank.hirc = [streamed, bnksfx.play_action(21, 11, 9), bnksfx.event(1, [21])]
    source = folder(tmp_path, {"snd/windows/st.bnk": bnk.write(bank), "snd/windows/media/77.wem": wem_pcm([5, 5])})
    (entry,) = run.export_path(source, "snd/windows/st.bnk", {"wav"}, tmp_path / "out")
    assert "snd/windows/st/media/77.wav" in entry.files and Path(tmp_path / "out/snd/windows/st/media/77.wem").is_file()
