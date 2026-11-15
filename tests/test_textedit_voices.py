"""The Texts app's voices for 1800, 2205, 1404 and 1701 (docs/text-editor.md, "Speakers and voices"): synthetic files,
and the installs where the ``ANNO*_DIR`` variables name them."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.fakes import INSTALL_VARS

from annotoolbox.assetedit.gamefiles import LAYOUTS, open_files
from annotoolbox.textedit.editfile import EditFile
from annotoolbox.textedit.textdoc import Filter, TextDoc
from test_textedit import _doc, _utf16, _write

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
    doc = _doc(files, "anno1800", "english")
    doc.prepare("english", "english")
    assert (doc.voice("256999").speaker, doc.voice("256999").event) == ("OldNate", 111)
    assert (doc.voice("257000").speaker, doc.voice("257000").event) == ("res", 0) and doc.voice("1") is None
    assert doc.rows(Filter(voiced=True)) == ["256999", "257000"]
    assert doc.playable("256999") == () and not doc.voices.can_play(
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
    doc = _doc(files, "anno2205", "english")
    doc.prepare("english", "english")
    assert (doc.voice("116088").speaker, doc.voice("116088").event) == ("ADAM", 42)
    assert (doc.voice("116089").speaker, doc.voice("116089").event) == ("Docks Rafferty", 43)
    assert not doc.playable("116088")  # no voice bank in the install: no button
    bank = "data/sound/generatedsoundbanks/windows/english(us)/1.bnk"
    again = _doc(_write(tmp_path, {bank: b""}), "anno2205", "english")  # the file list is read once: a fresh open
    again.prepare("english", "english")
    assert again.voices.can_play("116088", "english") and again.voices._bank_path("polish") == bank  # English stands in


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
    files = {"data/loca/eng/txt/a.txt": _utf16("5=Hello\r\n6=There\r\n7=Silent\r\n"), **dict.fromkeys(paths, b"ID3x")}
    doc = TextDoc(_write(tmp_path, files), LAYOUTS[game], EditFile(game), "english", "english")
    assert {doc.voice("5").speaker, doc.voice("6").speaker} == speakers and doc.voice("7") is None
    assert doc.playable("5") == ("source", "target") and doc.clip("6", "target")[1] == ".mp3"
    assert doc.rows(Filter(voiced=True)) == ["5", "6"]


# the installs
def _installed(game: str, tmp_path) -> TextDoc:
    folder = os.environ.get(INSTALL_VARS[game])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"needs {game}")
    doc = TextDoc(
        open_files(game, Path(folder), tmp_path / "cache"), LAYOUTS[game], EditFile(game), "english", "english"
    )
    doc.prepare("english", "english")
    return doc


@pytest.mark.parametrize(("game", "least"), [("anno1404", 6900), ("anno1701", 12000)])
def test_the_speech_of_1404_and_1701_is_found_and_plays(game, least, tmp_path):
    doc = _installed(game, tmp_path)
    lines = doc.voices.load("english")
    assert len(lines) >= least and sum(k in doc.table("english") for k in lines) > 1500
    key = next(k for k in lines if k in doc.table("english"))
    assert doc.clip(key, "source")[1] == ".mp3" and doc.voice(key).speaker


@pytest.mark.parametrize(("game", "least", "known"), [("anno1800", 15000, "res"), ("anno2205", 5900, "ADAM")])
def test_1800_and_2205_voice_lines_are_found_with_their_speaker_and_the_event(game, least, known, tmp_path):
    doc = _installed(game, tmp_path)
    lines = doc.voices.load("english")
    assert len(lines) >= least and known in {v.speaker for v in lines.values()}
    assert all(k in doc.table("english") for k in lines) and sum(bool(v.event) for v in lines.values()) > 5000
    key = next(k for k, v in lines.items() if v.event)
    try:
        doc.clip(key, "source")
    except LookupError as exc:  # the install holds no voice files (1800: no bank, 2205: no ``Voices`` folder)
        assert "voice" in str(exc)
