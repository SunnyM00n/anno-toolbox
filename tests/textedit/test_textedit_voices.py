"""The Texts app's voices for every game (docs/text-editor.md, "Speakers and voices", "Lip files", "1701's speech
files", "2205's streamed voices"): who speaks a text, the clip, the lip file beside it. Synthetic files,
and the installs where the ``ANNO*_DIR`` variables name them."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest
from tests.fakes import INSTALL_VARS
from tests.textedit.texteditfakes import AUDIO, META_TWINS, PORTRAIT, text_doc, text_export, utf16_bytes
from tests.textedit.texteditfakes import write_files as _write

from annotoolbox.assetedit.gamefiles import LAYOUTS, open_files
from annotoolbox.exporting import banks
from annotoolbox.textedit import lipsync, voicedata
from annotoolbox.textedit.editfile import CEditFile
from annotoolbox.textedit.textdoc import CTextDoc, SFilter

TEXTS = "<TextExport><Texts>{}</Texts></TextExport>"
ENTRY = "<Text><{tag}>{key}</{tag}><Text>{text}</Text></Text>"


def _audio_text(name: str, line: int, audio: int, guid: int) -> str:
    return (
        f"<Asset><Template>AudioText</Template><Values><Text><LocaText><English><Text>x</Text></English></LocaText>"
        f"<LineID>{line}</LineID></Text><AudioText><AudioAsset>{audio}</AudioAsset></AudioText>"
        f"<Standard><GUID>{guid}</GUID><Name>{name}</Name></Standard></Values></Asset>"
    )


def _audio(guid: int, wwise_tag: str, event: int) -> str:
    return (
        f"<Asset><Template>Audio</Template><Values><Standard><GUID>{guid}</GUID><Name>play_x</Name></Standard>"
        f"<Audio><{wwise_tag}>{event}</{wwise_tag}></Audio></Values></Asset>"
    )


def test_1800_names_a_spoken_text_by_an_audio_text_asset_whose_name_gives_the_speaker(tmp_path):
    assets = "data/config/export/main/asset/assets.xml"
    body = (
        _audio_text("AT_DLC03_OldNate_ArcticQ201_01", 8164, 900, 256999)
        + _audio_text("AT_res_tier05_Fire", 8165, 901, 257000)
        + _audio(900, "WwiseID", 111)
    )
    entries = "".join(
        ENTRY.format(tag="GUID", key=k, text=t) for k, t in (("256999", "Hi"), ("257000", "Fire"), ("1", "-"))
    )  # the text of an AudioText asset is keyed by the asset's GUID
    files = _write(
        tmp_path,
        {
            assets: f"<AssetList><Assets>{body}</Assets></AssetList>",
            "data/config/gui/texts_english.xml": TEXTS.format(entries),
        },
    )
    doc = text_doc(files, "anno1800", "english")
    doc.prepare("english", "english")
    assert (doc.voice("256999").speaker, doc.voice("256999").event) == ("OldNate", 111)
    assert (doc.voice("257000").speaker, doc.voice("257000").event) == ("res", 0) and doc.voice("1") is None
    assert doc.rows(SFilter(voiced=True)) == ["256999", "257000"]
    assert doc.playable("256999") == () and not doc.m_voices.can_play(
        "256999", "english"
    )  # the install has no voice bank
    with pytest.raises(LookupError, match="no voice bank"):
        doc.clip("256999", "source")


def test_2205_gives_a_text_the_character_group_of_its_file_and_the_event_of_its_audio(tmp_path):
    root = "data/config/game/asset/"
    groups = (
        "<Group><Groups><Group><Name>ADAM</Name><UseParentGUIDRange>1</UseParentGUIDRange>"
        f"<FileName>{root}texts/adam.xml</FileName><CharacterID>19</CharacterID></Group>"
        f"<Group><Name>Docks_Rafferty</Name><FileName>{root}texts/docks.xml</FileName></Group></Groups></Group>"
    )
    adam = f"<AssetList><Assets>{_audio_text('hello', 6874, 9800, 116088)}</Assets></AssetList>"
    docks = f"<AssetList><Assets>{_audio_text('ahoy', 6875, 9801, 116089)}</Assets></AssetList>"
    audio = (
        f"<AssetList><Assets>{_audio(9800, 'WwiseSoundID', 42)}{_audio(9801, 'WwiseSoundID', 43)}</Assets></AssetList>"
    )
    entries = "".join(ENTRY.format(tag="GUID", key=k, text=t) for k, t in (("116088", "Hello"), ("116089", "Ahoy")))
    files = _write(
        tmp_path,
        {
            root + "audio_texts.xml": groups, root + "texts/adam.xml": adam, root + "texts/docks.xml": docks,
            root + "audio_generated.xml": audio, "data/config/gui/texts_english.xml": TEXTS.format(entries),
        },
    )  # fmt: skip
    doc = text_doc(files, "anno2205", "english")
    doc.prepare("english", "english")
    assert (doc.voice("116088").speaker, doc.voice("116088").event) == ("ADAM", 42)
    assert (doc.voice("116089").speaker, doc.voice("116089").event) == ("Docks Rafferty", 43)
    assert not doc.playable("116088")  # no voice bank in the install: no button
    bank = "data/sound/generatedsoundbanks/windows/english(us)/1.bnk"
    again = text_doc(_write(tmp_path, {bank: b""}), "anno2205", "english")  # the file list is read once: a fresh open
    again.prepare("english", "english")
    assert (
        again.m_voices.can_play("116088", "english") and again.m_voices._bank_path("polish") == bank
    )  # English stands in


@pytest.mark.parametrize(
    ("game", "paths", "speakers"),
    [
        ("anno1404", ("data/loca/eng/speech/willem/5.mp3", "addondata/loca/eng/speech/giacomo/6.mp3"), {"willem", "giacomo"}),
        ("anno1701", ("data/loca/eng/speech/sprecher_3/5.mp3", "data/loca/eng/speech/addon01/addon_sprecher_1/6.mp3"),
         {"sprecher 3", "addon sprecher 1"}),
    ],
)  # fmt: skip
def test_the_speech_files_of_1404_and_1701_are_found_by_text_id_with_the_folder_as_speaker(
    tmp_path, game, paths, speakers
):
    files = {
        "data/loca/eng/txt/a.txt": utf16_bytes("5=Hello\r\n6=There\r\n7=Silent\r\n"),
        **dict.fromkeys(paths, b"ID3x"),
    }
    doc = CTextDoc(_write(tmp_path, files), LAYOUTS[game], CEditFile(game), "english", "english")
    assert {doc.voice("5").speaker, doc.voice("6").speaker} == speakers and doc.voice("7") is None
    assert doc.playable("5") == ("source", "target") and doc.clip("6", "target")[1] == ".mp3"
    assert doc.rows(SFilter(voiced=True)) == ["5", "6"]


def test_a_played_clip_is_one_file_of_the_cache_folder_however_often_it_plays(tmp_path):
    files = {"data/loca/eng/txt/a.txt": utf16_bytes("5=Hello\r\n"), "data/loca/eng/speech/willem/5.mp3": b"ID3x"}
    doc = CTextDoc(_write(tmp_path, files), LAYOUTS["anno1404"], CEditFile("anno1404"), "english", "english")
    first = doc.clip_file("5", "source")
    assert first.parent == tmp_path / "cache" / "sounds" and first.read_bytes() == b"ID3x" and first.suffix == ".mp3"
    first.write_bytes(b"kept")  # the second play opens the file that is there, it writes no other
    assert doc.clip_file("5", "target") == first and first.read_bytes() == b"kept"
    assert [p.name for p in first.parent.iterdir()] == [first.name]


@pytest.fixture
def voice_game(tmp_path):
    export = "data/base/config/export/"
    gui = "data/base/config/gui/"
    english = text_export(("-20", "Hello Marcus"), ("-21", "Hello Marcus"), ("-23", "Other"))
    return _write(
        tmp_path,
        {export + "audio_generated.xml": AUDIO, export + "assets.xml": PORTRAIT, gui + "texts_english.xml": english,
         gui + "texts_german.xml": english, gui + "texts_metadata.xml": META_TWINS},
    )  # fmt: skip


def test_the_speaker_of_a_117_line_is_the_portrait_of_the_character_its_audio_asset_names(voice_game):
    doc = text_doc(voice_game)
    doc.prepare("english", "german")
    voice = doc.voice("-20")
    assert (voice.speaker, voice.mood, voice.event) == ("Ben Baalion", "Neutral", 77)
    assert doc.voice("-23").speaker.startswith("character") and doc.voice("-21") is None
    assert doc.kind_label("-21") == "Marcus Africanus" and doc.playable("-20") == ("source", "target")
    assert doc.rows(SFilter(voiced=True)) == ["-20", "-23"] and doc.rows(SFilter(query="baalion")) == ["-20"]


def test_a_text_without_a_voice_line_says_so_when_asked_for_its_clip(voice_game):
    doc = text_doc(voice_game)
    with pytest.raises(LookupError, match="no voice line"):
        doc.clip("-21", "target")


def test_2070_speech_is_found_by_the_text_id_and_its_speaker_is_the_folder(tmp_path):
    files = _write(
        tmp_path,
        {
            "data/loca/eng/txt/a.txt": utf16_bytes("5=Hello\r\n6=Silent\r\n"),
            "data/loca/eng/speech/addon/eve/5.mp3": b"ID3fake",
            "data/loca/eng/speech/thor strindberg/5.mp3": b"ID3other",
        },
    )
    doc = CTextDoc(files, LAYOUTS["anno2070"], CEditFile("anno2070"), "english", "english")
    voice = doc.voice("5")
    assert voice.speaker in ("eve", "thor strindberg") and doc.voice("6") is None
    assert doc.clip("5", "source")[1] == ".mp3" and doc.rows(SFilter(voiced=True)) == ["5"]


# lip files
def _sp(levels: bytes) -> bytes:
    return struct.pack("<I", len(levels) - 4) + levels + bytes(4)


def test_a_lip_file_is_its_count_and_the_levels_and_a_clip_gives_the_rate():
    lips = lipsync.SLips.read(_sp(bytes([0, 128, 255, 255, 64, 0])))
    assert lips is not None and lips.levels == (0, 128, 255, 255, 64, 0) and lips.peak == 255
    assert lips.bars(3) == "▄█▂"  # the loudest of each third, in eight heights
    assert lipsync.SLips.read(b"short") is None and lipsync.SLips.read(struct.pack("<I", 9) + bytes(4)) is None
    frame = struct.pack(">I", 0xFFFB9000) + bytes(413)  # MPEG-1 layer III, 128 kbit/s, 44.1 kHz: 417 bytes, 26.1 ms
    assert lipsync.mp3_seconds(b"ID3" + bytes([4, 0, 0, 0, 0, 0, 0]) + frame * 10) == pytest.approx(0.2612, abs=1e-3)
    assert lipsync.mp3_seconds(b"not an mp3") == 0


def test_the_lip_file_beside_a_speech_file_is_shown_with_the_voice(tmp_path):
    frame = struct.pack(">I", 0xFFFB9000) + bytes(413)
    files = {
        "data/loca/eng/txt/a.txt": utf16_bytes("5=Hello\r\n"),
        "data/loca/eng/speech/willem/5.mp3": frame * 40,
        "data/loca/eng/speech/willem/5.sp": _sp(bytes(range(0, 100, 2))),
    }
    doc = CTextDoc(_write(tmp_path, files), LAYOUTS["anno1404"], CEditFile("anno1404"), "english", "english")
    line = doc.lips("5")
    assert "50 frames in 1.0 s" in line and len(doc.m_voices.load("english")) == 1
    assert CTextDoc(_write(tmp_path / "b", {**files, "data/loca/eng/speech/willem/5.sp": b"x"}), LAYOUTS["anno1404"],
                   CEditFile("anno1404"), "english", "english").lips("5") == ""  # fmt: skip


# 1701's speech files
MESSAGES = (
    "<MessageSounds><Message><ID>7</ID><SoundGuid>1</SoundGuid><SpeachGuid>900</SpeachGuid>"
    "<SpeachGuid>901</SpeachGuid></Message></MessageSounds>"
)


AI = (
    "<AI><MESSAGELIST><MESSAGE><Text>8</Text><SOUNDS><SOUND><GUID>910</GUID></SOUND><SOUND><GUID>911</GUID></SOUND>"
    "</SOUNDS></MESSAGE></MESSAGELIST></AI>"
)


def test_a_speech_file_no_text_names_belongs_to_the_text_its_message_or_ai_profile_gives_it(tmp_path):
    speech = "data/loca/eng/speech/"
    files = {
        "data/loca/eng/txt/a.txt": utf16_bytes("5=Own\r\n7=Message\r\n8=Ai profile\r\n9=Base\r\n10=Silent\r\n"),
        "data/config/game/messages.xml": MESSAGES, "data/ai/aiprofile.xml": AI,
        speech + "sprecher_1/5.mp3": b"x", speech + "sprecher_1/900.mp3": b"x", speech + "sprecher_2/901.mp3": b"x",
        speech + "sprecher_2/910.mp3": b"x", speech + "sprecher_2/911.mp3": b"x", speech + "sprecher_3/9_1.mp3": b"x",
        speech + "sprecher_3/9_2.mp3": b"x",
    }  # fmt: skip
    doc = CTextDoc(_write(tmp_path, files), LAYOUTS["anno1701"], CEditFile("anno1701"), "english", "english")
    doc.prepare("english", "english")
    assert [(k, doc.voice(k).speaker) for k in ("5", "7", "8", "9")] == [
        ("5", "sprecher 1"), ("7", "sprecher 1"), ("8", "sprecher 2"), ("9", "sprecher 3")
    ]  # fmt: skip
    assert (doc.voice("7").variants, doc.voice("9").variants, doc.voice("5").variants) == (2, 2, 1)
    assert doc.voice("10") is None and doc.playable("7") == ("source", "target")
    assert voicedata.read_speech_owners(doc.m_files, "anno2070") == {}


# 2205's streamed voice files
def test_a_bank_info_names_its_streamed_files_like_its_media():
    text = (
        '{"SoundBanksInfo": {"SoundBanks": [{"ShortName": "Voices", "ReferencedStreamedFiles": ['
        '{"Id": "479781", "ShortName": "109243@1161.wav", "Path": "Voices\\\\English(US)\\\\109243@1161_CC1486BC.wem"}]}]}}'
    )
    info = banks.bank_info(text)
    assert info.media[479781] == ("109243@1161.wav", "voices/english(us)/109243@1161_cc1486bc.wem")


# the installs
def _installed(game: str, tmp_path) -> CTextDoc:
    folder = os.environ.get(INSTALL_VARS[game])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"needs {game}")
    doc = CTextDoc(
        open_files(game, Path(folder), tmp_path / "cache"), LAYOUTS[game], CEditFile(game), "english", "english"
    )
    doc.prepare("english", "english")
    return doc


@pytest.mark.parametrize(("game", "least"), [("anno1404", 6900), ("anno1701", 12000)])
def test_the_speech_of_1404_and_1701_is_found_and_plays(game, least, tmp_path):
    doc = _installed(game, tmp_path)
    lines = doc.m_voices.load("english")
    assert len(lines) >= least and sum(k in doc.table("english") for k in lines) > 1500
    key = next(k for k in lines if k in doc.table("english"))
    assert doc.clip(key, "source")[1] == ".mp3" and doc.voice(key).speaker


@pytest.mark.parametrize(("game", "least", "known"), [("anno1800", 15000, "res"), ("anno2205", 5900, "ADAM")])
def test_1800_and_2205_voice_lines_are_found_with_their_speaker_and_the_event(game, least, known, tmp_path):
    doc = _installed(game, tmp_path)
    lines = doc.m_voices.load("english")
    assert len(lines) >= least and known in {v.speaker for v in lines.values()}
    assert all(k in doc.table("english") for k in lines) and sum(bool(v.event) for v in lines.values()) > 5000
    key = next(k for k, v in lines.items() if v.event)
    try:
        doc.clip(key, "source")
    except LookupError as exc:  # the install holds no voice files (1800: no bank, 2205: no ``Voices`` folder)
        assert "voice" in str(exc)


@pytest.mark.parametrize(("game", "least"), [("anno1404", 6500), ("anno1701", 11000)])
def test_the_lip_files_of_1404_and_1701_read_and_run_at_50_frames_a_second(game, least, tmp_path):
    files = _installed(game, tmp_path).m_files
    paths = files.glob("data/loca/eng/speech/*/*.sp") + files.glob("data/loca/eng/speech/*/*/*.sp")
    assert len(paths) >= least
    rates = []
    for path in paths[:200]:
        lips = lipsync.SLips.read(files.read(path))
        seconds = lipsync.mp3_seconds(files.read(path[:-3] + ".mp3"))
        assert lips is not None and seconds > 0
        rates.append(len(lips.levels) / seconds)
    assert all(48 < r < 54 for r in rates)


def test_1701s_speech_files_reach_the_texts_that_messages_and_ai_profiles_name(tmp_path):
    doc = _installed("anno1701", tmp_path)
    files = doc.m_files
    lines = doc.m_voices.load("english")
    voiced = [k for k in lines if k in doc.table("english")]
    assert len(voiced) > 2000 and any(lines[k].variants > 3 for k in voiced)
    assert voicedata.read_speech_owners(files, "anno1701")["5010321"] == "118056"
    assert "Lips:" in doc.m_voices.lip_line(next(k for k in voiced if lines[k].variants == 1), "english")


def test_2205s_missing_voice_file_is_named_by_the_banks_streamed_entry(tmp_path):
    doc = _installed("anno2205", tmp_path)
    lines = doc.m_voices.load("english")
    key = next(k for k, v in lines.items() if v.event and doc.m_voices.can_play(k, "english"))
    with pytest.raises(LookupError, match=r"voices/english\(us\)/.+\.wem"):
        doc.clip(key, "source")
