"""The text editor's readers and writers against the text files of the installed games (read-only, in memory): every
file of every language read, and written back byte for byte when nothing changes. Skips without the ``ANNO*_DIR``
variables."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from tests.fakes import INSTALL_VARS

from annotoolbox.assetedit.gamefiles import LAYOUTS, open_files
from annotoolbox.textedit import fontchain, render, textfiles, texttable
from annotoolbox.textedit.editfile import CEditFile, SEdit
from annotoolbox.textedit.textdoc import CTextDoc

GAMES = ("anno117", "anno1800", "anno2205", "anno2070", "anno1404", "anno1701")


def _files(game: str, tmp_path):
    folder = os.environ.get(INSTALL_VARS[game])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"needs {game} (its INSTALL_VARS variable)")
    return open_files(game, Path(folder), tmp_path / "cache")


@pytest.mark.parametrize("game", GAMES)
def test_every_text_file_of_every_language_is_read_whole_and_written_back_unchanged(game, tmp_path):
    files, layout = _files(game, tmp_path), LAYOUTS[game]
    languages = texttable.languages(files, layout)
    assert languages, f"{game} has no text files"
    style = texttable.style_of(layout)
    for language in languages:
        for path in texttable.paths_of(files, layout, language):
            raw = files.read(path)
            parsed = textfiles.parse(path, raw, style)
            assert parsed.render() == raw
            assert parsed.render({s.key: s.value for s in parsed.slots}) == raw, path  # its own texts change nothing
            if style == textfiles.XML:  # no entry slipped past the reader
                assert len(parsed.slots) == parsed.text.count(f"<{parsed.key_tag}>"), path
            else:
                assert len(parsed.slots) == len(re.findall(r"^[ \t]*[-\w.]+[ \t]*=", parsed.text, re.M)), path


@pytest.mark.parametrize("game", GAMES)
def test_a_translation_of_a_whole_language_is_written_in_one_pass_and_reads_back(game, tmp_path):
    files, layout = _files(game, tmp_path), LAYOUTS[game]
    languages = texttable.languages(files, layout)
    language = "english" if "english" in languages else next(iter(languages))
    loaded = texttable.read_language(files, layout, language)
    keys = list(loaded.table.by_id)[:: max(1, len(loaded.table) // 3000)]
    texts = {
        k: f"changed {n} <b>x</b>" if layout.text_format != "lines" else f"changed {n}" for n, k in enumerate(keys)
    }
    out = render.whole_files(loaded, layout, {**texts, "999999999": "a new text"}, "my-mod")
    assert out
    again = {}
    for path, data in out.items():
        parsed = textfiles.parse(path, data, texttable.style_of(layout))
        again |= {s.key: s.value for s in parsed.slots}
    assert all(again[k] == v for k, v in texts.items()) and again["999999999"] == "a new text"


def test_117s_english_texts_make_a_modops_with_every_edit_a_replace(tmp_path):
    files, layout = _files("anno117", tmp_path), LAYOUTS["anno117"]
    table = texttable.read_language(files, layout, "english").table
    keys = list(table.by_id)[:50]
    edits = CEditFile("anno117", "english", {"english": {k: SEdit("edited") for k in keys}})
    out = render.render(files, layout, edits, "my-mod", lambda _s: None)
    ops = out["data/base/config/gui/texts_english.xml"].decode()
    assert ops.count('Type="replace"') == 50 and 'Type="add"' not in ops


# the glyph check, 117's groups and twin lines, the voices (docs/text-editor.md, "Beyond the text")
def _doc(game: str, tmp_path, source="english", target="german") -> CTextDoc:
    files, layout = _files(game, tmp_path), LAYOUTS[game]
    doc = CTextDoc(files, layout, CEditFile(game), source, target)
    doc.prepare(doc.m_source, doc.m_target)
    return doc


@pytest.mark.parametrize("game", GAMES)
def test_every_font_file_of_a_game_reads_and_its_languages_lack_few_characters(game, tmp_path):
    doc = _doc(game, tmp_path, target="")
    check = doc.m_fonts
    assert check.paths(), f"{game} ships no font file"
    assert all(check.cover(p) for p in check.paths()), "a font file whose character table is empty or unread"
    for language in doc.m_languages:
        texts = (e.text for e in doc.table(language).entries)
        report = check.audit(language, texts)
        if game == "anno1800":  # two Latin-only styles are in every Asian language's list: only what no style draws
            report = [row for row in report if not row[2]]
        letters = [ch for ch, _n, _other in report if ord(ch) < 0x250]  # Latin letters with their accents
        latin = language not in (
            "japanese",
            "korean",
            "chinese",
            "taiwanese",
            "simplified_chinese",
            "traditional_chinese",
        )
        assert len(report) < 60, (language, report[:10])
        assert not (latin and letters) or (game == "anno1800" and letters == ["ǐ"]), (language, letters)  # one text


def test_117s_asian_faces_leave_the_accented_letters_to_their_fallbacks(tmp_path):
    check = _doc("anno117", tmp_path, target="japanese").m_fonts
    assert not check.audit("japanese", ["ç ê ñ"]), "the Latin face behind the Japanese one draws them"
    assert check.audit("japanese", [chr(0x378)]), "an unassigned character is in no chain"


def test_117s_variations_are_listed_behind_their_main_line_and_the_groups_have_no_name_in_the_data(tmp_path):
    doc = _doc("anno117", tmp_path)
    table = doc.table("english")
    assert sum(len(t) for t in table.twins.values()) > 2000 and all(m in table for m in table.twins)
    master, twins = next((m, t) for m, t in table.twins.items() if len(t) == 2)
    ids = doc.ids()
    at = ids.index(master)
    assert ids[at + 1 : at + 3] == twins and doc.twins_of(twins[0]) == [master, twins[1]]
    assert len({e.folder for e in table.entries if e.folder}) > 4000
    assert doc.kind_label(twins[0]), "a variation says what it is for"


def test_117s_voice_lines_are_found_with_their_speakers_and_one_plays(tmp_path):
    doc = _doc("anno117", tmp_path)
    lines = doc.m_voices.load("english")
    table = doc.table("english")
    assert len(lines) > 13000 and all(k in table for k in lines)
    assert sum(not v.speaker.startswith("character") for v in lines.values()) > len(lines) // 2
    key = next(iter(lines))
    try:
        data, suffix = doc.clip(key, "source")
    except Exception as exc:  # the Wwise Vorbis codebooks may not be installed: the other readers say so the same way
        pytest.skip(str(exc))
    assert suffix == ".ogg" and data.startswith(b"OggS")


def test_2070s_speech_is_found_by_text_id_with_its_speaker(tmp_path):
    doc = _doc("anno2070", tmp_path, target="english")
    lines = doc.m_voices.load("english")
    assert len(lines) > 6000 and sum(k in doc.table("english") for k in lines) >= len(lines) - 5
    assert {"eve", "thor strindberg"} <= {v.speaker for v in lines.values()}
    data, suffix = doc.clip(next(iter(lines)), "source")
    assert suffix == ".mp3" and len(data) > 1000


def test_117s_text_styles_draw_a_language_with_its_face_and_the_fallback_list_behind_it(tmp_path):
    from annotoolbox.textedit.fontchain import CFontChain

    chain = CFontChain(_files("anno117", tmp_path))
    assert chain.available
    names = {s.name: s for s in chain.styles("german")}
    assert {"AlbertusNova", "LisboaSans"} <= set(names)
    assert (
        names["AlbertusNova"].faces[0].endswith("albertusnovaforanno117tab.otf")
        and len(names["AlbertusNova"].faces) > 3
    )
    japanese = {s.name: s for s in chain.styles("japanese")}["AlbertusNova"].faces
    assert japanese[0].endswith("dfpkinggothicjp1-regular.ttf") and any(
        p.endswith("notosanskr-regular.ttf") for p in japanese
    )
    assert {s.name: s for s in chain.styles("korean")}["LisboaSans"].faces[0].endswith("notosanskr-regular.ttf")


def test_117s_stray_letters_of_other_scripts_are_drawn_by_the_fallbacks_so_no_text_style_lacks_them(tmp_path):
    check = _doc("anno117", tmp_path, target="german").m_fonts
    assert check.styles("german")
    assert not check.audit("german", ["Haus \u4e2d \u7684 \u00e7 \u00ea"]), (
        "the Asian fallbacks draw the stray Han letters"
    )
    assert [(ch, other) for ch, _n, other in check.audit("german", [chr(0x378)])] == [(chr(0x378), False)]


def test_1800s_four_scripted_localized_fonts_give_the_asian_languages_their_faces(tmp_path):
    chain = fontchain.CFontChain(_files("anno1800", tmp_path))
    assert chain.available and not chain._m_raw  # the schema decodes them now; the byte scan is a fallback
    japanese = {s.key: s for s in chain.styles("japanese")}
    assert sum(any(p.endswith(".ttc") for p in s.faces) for s in japanese.values()) == 4
    assert all(s.faces for s in chain.styles("korean")) and sum(s.styles for s in chain.styles("chinese")) > 100
    assert {s.faces[0] for s in chain.styles("taiwanese") if s.styles > 20} == {"data/fonts/dfpt_b5.ttf"}


@pytest.mark.parametrize(("game", "least"), [("anno117", 150), ("anno1800", 900)])
def test_the_labels_of_the_ui_files_link_texts_to_their_text_styles(game, least, tmp_path):
    files = _files(game, tmp_path)
    doc = CTextDoc(files, LAYOUTS[game], CEditFile(game), "english", "german")
    doc.prepare("english", "german")
    doc.prepare_styles()
    links = doc.m_links.load()
    assert len(links) >= least
    assert sum(k in doc.table("english") for k in links) > 0.8 * len(links)
    known = {key for key, _name in doc.style_choices()}
    assert all(set(fonts) <= known for fonts in links.values() if fonts)
    key = next(k for k in links if k in doc.table("english"))
    assert doc.linked_names(key) and doc.glyph_problems("Haus", key=key) == []
