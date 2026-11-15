"""The Texts app's fourth round (docs/text-editor.md, "One writer for both apps", "Text styles of a text", "Lip files",
"1701's speech files", "1800's font chain", "2205's streamed voices"): synthetic files, and the installs where the
``ANNO*_DIR`` variables name them."""

from __future__ import annotations

import os
import struct
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.fakes import INSTALL_VARS

from annotoolbox.assetedit import projectpart as assetpart
from annotoolbox.assetedit.changeset import ChangeSet
from annotoolbox.assetedit.gamefiles import LAYOUTS, open_files
from annotoolbox.exporting import banks
from annotoolbox.suite.project import Project
from annotoolbox.textedit import fontchain, fontcover, lipsync, merged, projectpart, stylelinks, voicedata
from annotoolbox.textedit.editfile import Edit, EditFile
from annotoolbox.textedit.textdoc import TextDoc
from test_textedit import GERMAN_117, XML_117, _doc, _utf16, _write
from test_textedit_audit import _fonts117


# one writer for both apps
def test_the_two_apps_texts_are_combined_and_a_text_both_changed_differently_is_a_conflict():
    edits = EditFile("anno117", "english", {"german": {"-13": Edit("Holzstamm", "Wood")}})
    combined, conflicts = merged.combine(edits, {"german": {"-13": "Balken", "-10": "Eins"}, "english": {"-13": "Log"}})
    assert {k: e.text for k, e in combined.texts["german"].items()} == {"-13": "Holzstamm", "-10": "Eins"}
    assert (
        combined.texts["english"]["-13"].text == "Log" and edits.count() == 1
    )  # the file's own edits stay as they are
    assert [(c.language, c.key, c.kept, c.dropped) for c in conflicts] == [("german", "-13", "Holzstamm", "Balken")]
    same, none = merged.combine(edits, {"german": {"-13": "Holzstamm"}})
    assert none == [] and same.count() == 1  # the very same text is no conflict


def _project_with_both(tmp_path):
    project = Project(tmp_path / "proj", mod_id="my-mod", game="anno117")
    EditFile("anno117", "english", {"german": {"-13": Edit("Holzstamm", "Wood")}}).save(projectpart.edits_path(project))
    changes = ChangeSet("anno117")
    changes.texts = {"german": {"-13": "Balken", "-10": "Eins"}}
    changes.save(assetpart.change_set_path(project))
    install = tmp_path / "game"
    (install / "data/base/config/gui").mkdir(parents=True)
    (install / "data/base/config/gui/texts_english.xml").write_text(XML_117, encoding="utf-8")
    (install / "data/base/config/gui/texts_german.xml").write_text(GERMAN_117, encoding="utf-8")
    return project, SimpleNamespace(anno117=str(install), workspace=str(tmp_path / "ws"))


def test_the_texts_part_writes_the_texts_of_both_apps_once_and_names_the_conflict(tmp_path):
    project, settings = _project_with_both(tmp_path)
    logged: list[str] = []
    out = projectpart.render(project, settings, logged.append)
    german = out["data/base/config/gui/texts_german.xml"].decode()
    assert "Holzstamm" in german and "Eins" in german and "Balken" not in german
    assert any("-13" in line and "differently" in line for line in logged)
    assert not assetpart.has_work(project)  # a change set that holds texts only has nothing for the assets part


# lip files
def _sp(levels: bytes) -> bytes:
    return struct.pack("<I", len(levels) - 4) + levels + bytes(4)


def test_a_lip_file_is_its_count_and_the_levels_and_a_clip_gives_the_rate():
    lips = lipsync.Lips.read(_sp(bytes([0, 128, 255, 255, 64, 0])))
    assert lips is not None and lips.levels == (0, 128, 255, 255, 64, 0) and lips.peak == 255
    assert lips.bars(3) == "▄█▂"  # the loudest of each third, in eight heights
    assert lipsync.Lips.read(b"short") is None and lipsync.Lips.read(struct.pack("<I", 9) + bytes(4)) is None
    frame = struct.pack(">I", 0xFFFB9000) + bytes(413)  # MPEG-1 layer III, 128 kbit/s, 44.1 kHz: 417 bytes, 26.1 ms
    assert lipsync.mp3_seconds(b"ID3" + bytes([4, 0, 0, 0, 0, 0, 0]) + frame * 10) == pytest.approx(0.2612, abs=1e-3)
    assert lipsync.mp3_seconds(b"not an mp3") == 0


def test_the_lip_file_beside_a_speech_file_is_shown_with_the_voice(tmp_path):
    frame = struct.pack(">I", 0xFFFB9000) + bytes(413)
    files = {
        "data/loca/eng/txt/a.txt": _utf16("5=Hello\r\n"),
        "data/loca/eng/speech/willem/5.mp3": frame * 40,
        "data/loca/eng/speech/willem/5.sp": _sp(bytes(range(0, 100, 2))),
    }
    doc = TextDoc(_write(tmp_path, files), LAYOUTS["anno1404"], EditFile("anno1404"), "english", "english")
    line = doc.lips("5")
    assert "50 frames in 1.0 s" in line and len(doc.voices.load("english")) == 1
    assert TextDoc(_write(tmp_path / "b", {**files, "data/loca/eng/speech/willem/5.sp": b"x"}), LAYOUTS["anno1404"],
                   EditFile("anno1404"), "english", "english").lips("5") == ""  # fmt: skip


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
        "data/loca/eng/txt/a.txt": _utf16("5=Own\r\n7=Message\r\n8=Ai profile\r\n9=Base\r\n10=Silent\r\n"),
        "data/config/game/messages.xml": MESSAGES, "data/ai/aiprofile.xml": AI,
        speech + "sprecher_1/5.mp3": b"x", speech + "sprecher_1/900.mp3": b"x", speech + "sprecher_2/901.mp3": b"x",
        speech + "sprecher_2/910.mp3": b"x", speech + "sprecher_2/911.mp3": b"x", speech + "sprecher_3/9_1.mp3": b"x",
        speech + "sprecher_3/9_2.mp3": b"x",
    }  # fmt: skip
    doc = TextDoc(_write(tmp_path, files), LAYOUTS["anno1701"], EditFile("anno1701"), "english", "english")
    doc.prepare("english", "english")
    assert [(k, doc.voice(k).speaker) for k in ("5", "7", "8", "9")] == [
        ("5", "sprecher 1"), ("7", "sprecher 1"), ("8", "sprecher 2"), ("9", "sprecher 3")
    ]  # fmt: skip
    assert (doc.voice("7").variants, doc.voice("9").variants, doc.voice("5").variants) == (2, 2, 1)
    assert doc.voice("10") is None and doc.playable("7") == ("source", "target")
    assert voicedata.read_speech_owners(doc.files, "anno2070") == {}


# 1800's font chain
def _ref(guid: str) -> bytes:
    return bytes([1]) + bytes.fromhex("36c9a85f") + uuid.UUID(guid).bytes_le


def test_a_localized_font_the_schema_does_not_decode_is_read_by_the_references_it_holds():
    ft, ft2, ec = (str(uuid.uuid4()) for _ in range(3))
    exception = bytes([2, 0]) + bytes.fromhex("95a87446") + bytes.fromhex("097f80") + bytes([6, 1]) + b"Korean"
    exception2 = bytes([2, 0]) + bytes.fromhex("95a87446") + bytes.fromhex("097f80") + bytes([7, 1]) + b"Chinese"
    data = b"PHXLF" + _ref(ft) + exception + _ref(ft2) + bytes(5) + exception2 + _ref(ec) + _ref(str(uuid.uuid4()))
    raw = fontchain.scan_localized(data, {ft, ft2, ec})
    assert raw.base == [ft] and raw.exceptions == {"korean": [ft2], "chinese": [ec]}  # a stray reference is no font


# the style of a text
def test_a_label_names_its_text_by_hex_digits_in_117_and_a_decimal_guid_in_1800():
    assert stylelinks.text_id("A018 4AB6 6641 96B8") == str(0xA0184AB6664196B8 - (1 << 64))
    assert stylelinks.text_id("0001 0000 0000 0001") == str(0x0001000000000001)
    assert stylelinks.text_id("22537") == "22537" and stylelinks.text_id("") == "" and stylelinks.text_id("12") == "12"
    assert stylelinks.text_id("zzzz zzzz zzzz zzzz") == ""


class _KeyedChain:
    available = True

    def styles(self, language):
        head, body = "data/fonts/albertusnovaforanno117tab.otf", "data/fonts/lisboasans.otf"
        return [
            fontchain.StyleFaces("AlbertusNova", (head,), 20, "lf-head"),
            fontchain.StyleFaces("LisboaSans", (body,), 14, "lf-body"),
        ]


def test_a_text_is_checked_in_the_styles_of_its_labels_and_the_picker_can_name_another(tmp_path):
    files = _fonts117(tmp_path, latin=(0x20, 0x7E), body=(0x20, 0x5A))
    doc = _doc(files)
    doc.fonts = fontcover.FontCheck(files, "anno117", _KeyedChain())
    doc.links = SimpleNamespace(fonts_of=lambda key: ("lf-body",) if key == "-13" else (), load=lambda: None)
    doc.prepare("english", "german")
    assert doc.style_choices() == [("lf-head", "AlbertusNova"), ("lf-body", "LisboaSans")]
    assert [p.severity for p in doc.glyph_problems("u", key="-13")] == ["warning"]  # the label's style lacks it
    assert [p.severity for p in doc.glyph_problems("u", key="-10")] == ["hint"]  # no link: every style, one draws it
    assert doc.glyph_problems("u", "lf-head", "-13") == [] and [
        p.severity for p in doc.glyph_problems("u", "*", "-13")
    ] == ["hint"]
    assert doc.linked_names("-13") == ["LisboaSans"] and doc.linked_names("-10") == []


# 2205's streamed voice files
def test_a_bank_info_names_its_streamed_files_like_its_media():
    text = (
        '{"SoundBanksInfo": {"SoundBanks": [{"ShortName": "Voices", "ReferencedStreamedFiles": ['
        '{"Id": "479781", "ShortName": "109243@1161.wav", "Path": "Voices\\\\English(US)\\\\109243@1161_CC1486BC.wem"}]}]}}'
    )
    info = banks.bank_info(text)
    assert info.media[479781] == ("109243@1161.wav", "voices/english(us)/109243@1161_cc1486bc.wem")


# the installs
def _installed(game: str, tmp_path):
    folder = os.environ.get(INSTALL_VARS[game])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"needs {game}")
    return open_files(game, Path(folder), tmp_path / "cache")


@pytest.mark.parametrize(("game", "least"), [("anno1404", 6500), ("anno1701", 11000)])
def test_the_lip_files_of_1404_and_1701_read_and_run_at_50_frames_a_second(game, least, tmp_path):
    files = _installed(game, tmp_path)
    paths = files.glob("data/loca/eng/speech/*/*.sp") + files.glob("data/loca/eng/speech/*/*/*.sp")
    assert len(paths) >= least
    rates = []
    for path in paths[:200]:
        lips = lipsync.Lips.read(files.read(path))
        seconds = lipsync.mp3_seconds(files.read(path[:-3] + ".mp3"))
        assert lips is not None and seconds > 0
        rates.append(len(lips.levels) / seconds)
    assert all(48 < r < 54 for r in rates)


def test_1701s_speech_files_reach_the_texts_that_messages_and_ai_profiles_name(tmp_path):
    files = _installed("anno1701", tmp_path)
    doc = TextDoc(files, LAYOUTS["anno1701"], EditFile("anno1701"), "english", "english")
    doc.prepare("english", "english")
    lines = doc.voices.load("english")
    voiced = [k for k in lines if k in doc.table("english")]
    assert len(voiced) > 2000 and any(lines[k].variants > 3 for k in voiced)
    assert voicedata.read_speech_owners(files, "anno1701")["5010321"] == "118056"
    assert "Lips:" in doc.voices.lip_line(next(k for k in voiced if lines[k].variants == 1), "english")


def test_1800s_four_scripted_localized_fonts_give_the_asian_languages_their_faces(tmp_path):
    chain = fontchain.FontChain(_installed("anno1800", tmp_path))
    assert chain.available and not chain._raw  # the schema decodes them now; the byte scan is a fallback
    japanese = {s.key: s for s in chain.styles("japanese")}
    assert sum(any(p.endswith(".ttc") for p in s.faces) for s in japanese.values()) == 4
    assert all(s.faces for s in chain.styles("korean")) and sum(s.styles for s in chain.styles("chinese")) > 100
    assert {s.faces[0] for s in chain.styles("taiwanese") if s.styles > 20} == {"data/fonts/dfpt_b5.ttf"}


@pytest.mark.parametrize(("game", "least"), [("anno117", 150), ("anno1800", 900)])
def test_the_labels_of_the_ui_files_link_texts_to_their_text_styles(game, least, tmp_path):
    files = _installed(game, tmp_path)
    doc = TextDoc(files, LAYOUTS[game], EditFile(game), "english", "german")
    doc.prepare("english", "german")
    doc.prepare_styles()
    links = doc.links.load()
    assert len(links) >= least
    assert sum(k in doc.table("english") for k in links) > 0.8 * len(links)
    known = {key for key, _name in doc.style_choices()}
    assert all(set(fonts) <= known for fonts in links.values() if fonts)
    key = next(k for k in links if k in doc.table("english"))
    assert doc.linked_names(key) and doc.glyph_problems("Haus", key=key) == []


def test_2205s_missing_voice_file_is_named_by_the_banks_streamed_entry(tmp_path):
    files = _installed("anno2205", tmp_path)
    doc = TextDoc(files, LAYOUTS["anno2205"], EditFile("anno2205"), "english", "english")
    doc.prepare("english", "english")
    lines = doc.voices.load("english")
    key = next(k for k, v in lines.items() if v.event and doc.voices.can_play(k, "english"))
    with pytest.raises(LookupError, match=r"voices/english\(us\)/.+\.wem"):
        doc.clip(key, "source")
