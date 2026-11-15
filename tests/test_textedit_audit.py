"""The text editor's audit of 2026-11-13: faults found in the first build (lossless writing, line breaks of the line
files, the outdated marker, odd CSV, XLIFF and PO input) and the four leftovers (the glyph check, 117's text groups and
twin lines, speakers and voices), on synthetic files."""

from __future__ import annotations

import struct

import pytest

from annotoolbox.assetedit.gamefiles import LAYOUTS
from annotoolbox.textedit import exchange, fontchain, fontcover, render, textfiles, texttable
from annotoolbox.textedit.editfile import Edit, EditFile
from annotoolbox.textedit.textdoc import CHANGED, OUTDATED, TRANSLATED, UNTRANSLATED, Filter, TextDoc
from test_textedit import GERMAN_117, XML_117, _doc, _utf16, _write


# the files
def test_a_file_with_a_broken_byte_is_written_back_with_that_byte_when_another_text_changes():
    raw = b"<TextExport><Texts><Text><LineId>1</LineId><Text>a\xffb</Text></Text><Text><LineId>2</LineId><Text>x</Text></Text></Texts></TextExport>"
    parsed = textfiles.parse("t.xml", raw, textfiles.XML)
    assert parsed.render({"2": "y"}) == raw.replace(b">x<", b">y<")
    utf16 = b"\xff\xfe" + "1=a".encode("utf-16-le") + b"\x00\xd8" + "\n2=x\n".encode("utf-16-le")  # a lone surrogate
    lines = textfiles.parse("t.txt", utf16, textfiles.LINES)
    assert lines.render({"2": "y"}) == utf16.replace(b"x\x00", b"y\x00")


def test_an_entity_that_is_no_character_is_left_as_it_is_and_does_not_stop_the_file_from_reading():
    raw = b"<TextExport><Texts><Text><LineId>1</LineId><Text>a&#xD800;b&#0;c&#1114112;d&#65;</Text></Text></Texts></TextExport>"
    value = textfiles.parse("t.xml", raw, textfiles.XML).slots[0].value
    assert value == "a&#xD800;b&#0;c&#1114112;dA"


def test_a_line_file_never_gets_a_real_line_break_which_would_start_another_line():
    parsed = textfiles.parse("t.txt", _utf16("1=a\r\n2=b\r\n"), textfiles.LINES)
    out = parsed.render({"1": "x\r\ny\nz"}, {"3": "p\nq"})[2:].decode("utf-16-le")
    assert out == "1=x<br/>y<br/>z\r\n2=b\r\n3=p<br/>q\r\n"


def test_a_new_id_of_a_line_game_is_one_line_in_the_mods_own_file(tmp_path):
    files = _write(tmp_path, {"data/loca/eng/txt/a.txt": _utf16("1=a\r\n")})
    edits = EditFile("anno2070", "english", {"english": {"9": Edit("two\nlines")}})
    out = render.render(files, LAYOUTS["anno2070"], edits, "my-mod", lambda _s: None)
    assert out["data/loca/eng/txt/my-mod.txt"][2:].decode("utf-16-le") == "9=two<br/>lines\r\n"


# the document
def test_applying_a_text_that_is_already_there_is_no_edit_to_undo_or_to_save(tmp_path):
    doc = _doc(_write(tmp_path, {"data/base/config/gui/texts_english.xml": XML_117,
                                 "data/base/config/gui/texts_german.xml": GERMAN_117}))  # fmt: skip
    doc.edit("-13", "Holz")
    assert not doc.undo_stack and not doc.dirty
    doc.edit("-13", "Holzstamm")
    doc.dirty = False
    doc.edit("-13", "Holzstamm")
    assert len(doc.undo_stack) == 1 and not doc.dirty


def test_a_translation_typed_again_after_the_source_was_edited_confirms_it_instead_of_staying_outdated(tmp_path):
    doc = _doc(_write(tmp_path, {"data/base/config/gui/texts_english.xml": XML_117,
                                 "data/base/config/gui/texts_german.xml": GERMAN_117}))  # fmt: skip
    doc.edit("-13", "Wood and more", "english")
    assert doc.status("-13") == OUTDATED
    assert doc.edit("-13", "Holz") == CHANGED  # the person read the new source and kept the translation
    assert doc.edits.get("german", "-13").source == "Wood and more"
    assert doc.revert("-13") == OUTDATED


def test_a_text_with_windows_line_ends_is_stored_with_the_plain_break(tmp_path):
    doc = _doc(_write(tmp_path, {"data/base/config/gui/texts_english.xml": XML_117,
                                 "data/base/config/gui/texts_german.xml": GERMAN_117}))  # fmt: skip
    doc.edit("-13", "a\r\nb\rc")
    assert doc.target_text("-13") == "a\nb\nc"
    out = render.render(doc.files, doc.layout, doc.edits, "m", lambda _s: None)["data/base/config/gui/texts_german.xml"]
    assert b"\r\r" not in out


# the exchange formats
def test_a_po_text_with_characters_python_takes_for_line_ends_comes_back_whole():
    odd = "a b\x85c\x0cd\x1ce"
    rows = [exchange.Row("-1", odd, odd + "!", "f.xml", "", "")]
    back = exchange.read("po", exchange.write("po", rows, "g", "english", "german"))
    assert (back[0].source, back[0].target) == (odd, odd + "!")


def test_a_po_file_without_blank_lines_between_entries_and_with_a_header_and_plurals_reads():
    po = (
        b'msgid ""\nmsgstr ""\n"Language: de\\n"\n\n'
        b'#, fuzzy\nmsgctxt "-1"\nmsgid "One"\nmsgstr "Eins"\nmsgctxt "-2"\nmsgid "Two"\nmsgid_plural "Twos"\n'
        b'msgstr[0] "Zwei"\nmsgstr ""\n"zwei"\n#~ msgid "gone"\n'
    )
    rows = exchange.read("po", po)
    assert [(r.id, r.target, r.status) for r in rows] == [("-1", "Eins", OUTDATED), ("-2", "zwei", "")]


def test_a_csv_from_a_german_spreadsheet_is_read_with_its_semicolons_and_in_utf16():
    text = 'ID;Target;Source\r\n-1;Eins, zwei;One\r\n-2;"a;b\r\nc";Two\r\n'
    for data in (text.encode("utf-8-sig"), text.encode("utf-16")):
        rows = exchange.read("csv", data)
        assert [(r.id, r.target, r.source) for r in rows] == [("-1", "Eins, zwei", "One"), ("-2", "a;b\r\nc", "Two")]
    with pytest.raises(ValueError, match="columns"):
        exchange.read("csv", b"")
    tabbed = "id\ttarget\r\n-1\tx\r\n".encode("utf-16")
    assert exchange.read("csv", tabbed)[0].target == "x"


def test_a_csv_status_column_is_not_taken_for_a_review_request():
    rows = exchange.read("csv", b"id,target,status\r\n-1,x,outdated\r\n")
    assert rows[0].status == ""


def test_xliff_2_is_read_and_a_unit_that_asks_for_a_review_becomes_outdated():
    v2 = (
        b'<xliff version="2.0" xmlns="urn:oasis:names:tc:xliff:document:2.0" srcLang="en" trgLang="de"><file id="f">'
        b'<unit id="-1"><segment state="translated"><source>One</source><target>Eins</target></segment></unit>'
        b'<unit id="-2"><segment state="initial"><source>Two</source><target>Zwei</target></segment></unit></file></xliff>'
    )
    assert [(r.id, r.target, r.status) for r in exchange.read("xliff", v2)] == [("-1", "Eins", ""), ("-2", "Zwei", "")]
    v1 = (
        b'<xliff version="1.2"><file><body><trans-unit id="-1"><source>a</source>'
        b'<target state="needs-review-translation">b</target></trans-unit></body></file></xliff>'
    )
    assert exchange.read("xliff", v1)[0].status == OUTDATED


def test_an_imported_review_request_marks_the_text_outdated_and_a_text_twice_counts_once(tmp_path):
    doc = _doc(_write(tmp_path, {"data/base/config/gui/texts_english.xml": XML_117,
                                 "data/base/config/gui/texts_german.xml": GERMAN_117}))  # fmt: skip
    rows = [
        exchange.Row("-13", "Wood", "Erster", status=OUTDATED),
        exchange.Row("-13", "Wood", "Holzscheit", status=OUTDATED),
        exchange.Row("-10", "", "Speichern {0} <b>jetzt</b>x\r\ny"),
    ]
    result = exchange.apply(doc, rows)
    assert result.applied == 2 and doc.target_text("-13") == "Holzscheit" and doc.status("-13") == OUTDATED
    assert doc.target_text("-10").endswith("x\ny")
    assert doc.edit("-13", "Holzscheit") == CHANGED  # the person applying the text confirms it: the request is cleared


def test_a_new_text_added_by_an_import_without_a_source_column_has_no_empty_source_edit(tmp_path):
    doc = _doc(_write(tmp_path, {"data/base/config/gui/texts_english.xml": XML_117,
                                 "data/base/config/gui/texts_german.xml": GERMAN_117}))  # fmt: skip
    result = exchange.apply(doc, [exchange.Row("-900", "", "Neu")], add_new=True)
    assert result.added == 1 and doc.edits.get("english", "-900") is None and doc.target_text("-900") == "Neu"


def test_an_edit_file_with_odd_types_is_read_as_text_not_kept_as_it_is(tmp_path):
    path = tmp_path / "e.json"
    path.write_text(
        '{"format": 1, "texts": {"german": {"7": {"text": 5, "source": 6}}}, "glossary": {"german": {"a": 1}}}'
    )
    edits = EditFile.load(path)
    assert edits.get("german", "7") == Edit("5", "6") and edits.glossary == {"german": {"a": "1"}}
    path.write_text('{"format": 1, "glossary": {"german": ["a"]}}')
    with pytest.raises(ValueError, match="not a text edit file"):
        EditFile.load(path)


# the glyph check
def _font(*ranges: tuple[int, int], wide: bool = False, base: int = 0) -> bytes:
    """A font file with only a character table (``cmap``): format 4 for the basic plane, format 12 when ``wide``;
    ``base``: where the file starts inside a collection (table offsets count from the collection's start)."""
    if wide:
        groups = b"".join(struct.pack(">III", a, b, 1) for a, b in ranges)
        sub = struct.pack(">HHII", 12, 0, 16 + len(groups), 0) + struct.pack(">I", len(ranges)) + groups
    else:
        segs = [*ranges, (0xFFFF, 0xFFFF)]
        n = len(segs)
        ends = b"".join(struct.pack(">H", b) for _a, b in segs)
        starts = b"".join(struct.pack(">H", a) for a, _b in segs)
        deltas = b"".join(struct.pack(">H", (1 - a) & 0xFFFF) for a, _b in segs)
        sub = struct.pack(">HHHHHHH", 4, 16 + 8 * n, 0, 2 * n, 0, 0, 0) + ends + b"\0\0" + starts + deltas + b"\0\0" * n
    cmap = struct.pack(">HHHHI", 0, 1, 3, 10 if wide else 1, 12) + sub
    table = struct.pack(">4sIII", b"cmap", 0, base + 28, len(cmap))
    return struct.pack(">IHHHH", 0x10000, 1, 16, 0, 0) + table + cmap


def test_the_character_table_of_a_font_and_of_a_collection_is_read():
    assert fontcover.covered(_font((0x41, 0x5A))) == frozenset(range(0x41, 0x5B))
    assert 0x1F600 in fontcover.covered(_font((0x1F600, 0x1F602), wide=True))
    one, two = _font((0x41, 0x42), base=20), _font((0x61, 0x62), base=20 + len(_font((0x41, 0x42))))
    head = struct.pack(">4sHHI", b"ttcf", 1, 0, 2) + struct.pack(">II", 20, 20 + len(one))
    assert fontcover.covered(head + one + two) == {0x41, 0x42, 0x61, 0x62}
    with pytest.raises(ValueError, match="not a font file"):
        fontcover.covered(b"junk")


def _fonts117(tmp_path, latin=(0x20, 0x7E), body=(0x20, 0x7E), jp=(0x3040, 0x30FF)):
    gui = "data/base/config/gui/"
    return _write(
        tmp_path,
        {
            "data/fonts/albertusnovaforanno117tab.otf": _font(latin),
            "data/fonts/lisboasans.otf": _font(body),
            "data/fonts/dfpkinggothicjp1-regular.ttf": _font(jp),
            "data/fonts/roboto-regular.ttf": _font((0x20, 0x7E), (0xE0, 0xFF)),
            gui + "texts_english.xml": XML_117,
            gui + "texts_german.xml": GERMAN_117,
            gui + "texts_japanese.xml": GERMAN_117,
        },
    )


class _Chain:
    """What ``FontChain`` answers for 117: the head and body style, each with the files it tries per language."""

    available = True

    def styles(self, language):
        head, body = "data/fonts/albertusnovaforanno117tab.otf", "data/fonts/lisboasans.otf"
        jp = "data/fonts/dfpkinggothicjp1-regular.ttf"
        if language == "japanese":
            return [
                fontchain.StyleFaces("AlbertusNova", (jp, head), 20),
                fontchain.StyleFaces("LisboaSans", (jp, body), 14),
            ]
        return [
            fontchain.StyleFaces("AlbertusNova", (head, jp), 20),
            fontchain.StyleFaces("LisboaSans", (body, jp), 14),
        ]


def test_a_character_no_style_draws_is_a_warning_and_one_only_some_styles_draw_is_a_hint(tmp_path):
    files = _fonts117(tmp_path, body=(0x20, 0x5A))
    check = fontcover.FontCheck(files, "anno117", _Chain())
    # the body face ends at Z, so a lower-case letter lacks in the body style only (the headline style draws it)
    german = {p.message.split(" ")[0]: p.severity for p in check.unsupported("german", "Fuß <b>{0}</b> [Tag ü] é a")}
    assert german == {"u": "hint", "a": "hint", "ß": "warning", "é": "warning"}
    assert {p.message.split(" ")[0]: p.severity for p in check.unsupported("german", "AZ")} == {}
    assert {p.severity for p in check.unsupported("german", "u", "LisboaSans")} == {"warning"}  # in that style alone
    assert not check.unsupported("german", "u", "AlbertusNova") and check.styles("german") == [
        "AlbertusNova",
        "LisboaSans",
    ]
    japanese = {p.message.split(" ")[0]: p.severity for p in check.unsupported("japanese", "あ A é")}
    assert japanese == {
        "é": "warning"
    }  # the Japanese face and the Latin fallbacks draw the rest; Roboto is in no chain
    silent = "AB" + chr(0x200B) + " " + chr(10)
    assert not check.unsupported("german", silent)  # white space and the zero-width space draw nothing


def test_a_game_whose_faces_are_not_read_checks_against_every_font_it_ships(tmp_path):
    files = _write(
        tmp_path, {"data/fonts/a.ttf": _font((0x20, 0x7E)), "data/config/gui/texts_english.xml": "<TextExport/>"}
    )
    check = fontcover.FontCheck(files, "anno1800")
    assert [p.severity for p in check.unsupported("english", "ok ß")] == ["warning"]
    assert check.audit("english", ["ß ß", "a ß", "é"]) == [("ß", 2, False), ("é", 1, False)]


def test_the_document_reports_the_glyphs_of_a_target_once_its_fonts_are_read(tmp_path):
    files = _fonts117(tmp_path)
    doc = _doc(files)
    doc.edit("-13", "Holz ß")
    assert doc.glyph_problems("ß") == []  # not yet read: nothing is asked of the files on the window's thread
    doc.prepare("english", "german")
    assert [p.kind for p in doc.problems("-13")] == ["glyph"] and doc.problems("-10") == []


# 117's text groups and twin lines
META_TWINS = (
    "<TextExport><Texts>"
    "<Text><LineId>-20</LineId><Name>Quest Hello</Name><FolderId>-777</FolderId></Text>"
    "<Text><LineId>-21</LineId><Name>Quest Hello</Name><FolderId>-777</FolderId><MasterId>-20</MasterId><VariationId>-900</VariationId></Text>"
    "<Text><LineId>-22</LineId><Name>Quest Hello</Name><FolderId>-777</FolderId><MasterId>-20</MasterId><VariationId>-901</VariationId></Text>"
    "<Text><LineId>-23</LineId><Name>Other</Name><FolderId>-778</FolderId></Text>"
    "</Texts></TextExport>"
)


def _entries(*items: tuple[str, str]) -> str:
    body = "".join(f"<Text><LineId>{k}</LineId><Text>{v}</Text></Text>" for k, v in items)
    return f"<TextExport><Texts>{body}</Texts></TextExport>"


@pytest.fixture
def twin_game(tmp_path):
    gui = "data/base/config/gui/"
    english = _entries(("-22", "Hello Marcia"), ("-23", "Other"), ("-20", "Hello Marcus"), ("-21", "Hello Marcus"))
    german = _entries(("-20", "Hello Marcus"), ("-21", "Hello Marcus"), ("-22", "Hello Marcia"), ("-23", "Anderes"))
    return _write(
        tmp_path,
        {gui + "texts_english.xml": english, gui + "texts_german.xml": german, gui + "texts_metadata.xml": META_TWINS},
    )


def test_the_metadata_names_the_group_the_main_line_and_the_kind_of_a_variation(twin_game):
    table = texttable.read_language(
        twin_game, LAYOUTS["anno117"], "english", texttable.read_names(twin_game, LAYOUTS["anno117"])
    ).table
    assert table.get("-21").master == "-20" and table.get("-21").kind == "-900" and table.get("-20").folder == "-777"
    assert table.twins == {"-20": ["-22", "-21"]}  # in the file's order


def test_a_variation_is_listed_behind_its_main_line_and_both_know_each_other(twin_game):
    doc = _doc(twin_game)
    assert doc.ids() == ["-23", "-20", "-22", "-21"]  # the file lists -22 first: it stands behind -20 all the same
    assert doc.twins_of("-20") == ["-22", "-21"] and doc.twins_of("-21") == ["-20", "-22"] and doc.twins_of("-23") == []


def test_a_translation_fills_the_twins_that_have_the_very_same_source_only(twin_game):
    doc = _doc(twin_game)
    doc.edit("-20", "Hallo Marcus")
    assert doc.fill_twins("-20") == 1
    assert doc.target_text("-21") == "Hallo Marcus" and doc.target_text("-22") == "Hello Marcia"
    assert doc.undo().startswith("fill the twins") and doc.target_text("-21") == "Hello Marcus"
    assert doc.status("-21") == UNTRANSLATED and doc.status("-20") == CHANGED and TRANSLATED


# speakers and voices
AUDIO = (
    "<Group><Assets><Asset><Template>Audio</Template><Values><Standard><GUID>1</GUID><Name>Line_A</Name></Standard>"
    "<Audio><VoiceMood>Neutral</VoiceMood><CharacterId>-555</CharacterId></Audio>"
    "<WwiseStandard><WwiseID>77</WwiseID><OasisScriptId>-20</OasisScriptId></WwiseStandard></Values></Asset>"
    "<Asset><Template>Audio</Template><Values><Standard><GUID>2</GUID><Name>Line_B</Name></Standard>"
    "<Audio><CharacterId>-999</CharacterId></Audio>"
    "<WwiseStandard><WwiseID>78</WwiseID><OasisScriptId>-23</OasisScriptId></WwiseStandard></Values></Asset></Assets></Group>"
)
PORTRAIT = (
    "<Assets><Asset><Template>Portrait</Template><Values><Portrait><PortraitName>-1</PortraitName>"
    "<OasisCharacterId>-555</OasisCharacterId></Portrait><Standard><GUID>9</GUID>"
    "<Name>Advisor Character (Ben Baalion) Portrait</Name></Standard></Values></Asset>"
    "<Asset><Template>Portrait</Template><Values><Portrait><OasisCharacterId>-900</OasisCharacterId></Portrait>"
    "<Standard><GUID>10</GUID><Name>Campaign Char01 (Marcus Africanus)</Name></Standard></Values></Asset></Assets>"
)


@pytest.fixture
def voice_game(tmp_path):
    export = "data/base/config/export/"
    gui = "data/base/config/gui/"
    english = _entries(("-20", "Hello Marcus"), ("-21", "Hello Marcus"), ("-23", "Other"))
    return _write(
        tmp_path,
        {export + "audio_generated.xml": AUDIO, export + "assets.xml": PORTRAIT, gui + "texts_english.xml": english,
         gui + "texts_german.xml": english, gui + "texts_metadata.xml": META_TWINS},
    )  # fmt: skip


def test_the_speaker_of_a_117_line_is_the_portrait_of_the_character_its_audio_asset_names(voice_game):
    doc = _doc(voice_game)
    doc.prepare("english", "german")
    voice = doc.voice("-20")
    assert (voice.speaker, voice.mood, voice.event) == ("Ben Baalion", "Neutral", 77)
    assert doc.voice("-23").speaker.startswith("character") and doc.voice("-21") is None
    assert doc.kind_label("-21") == "Marcus Africanus" and doc.playable("-20") == ("source", "target")
    assert doc.rows(Filter(voiced=True)) == ["-20", "-23"] and doc.rows(Filter(query="baalion")) == ["-20"]


def test_a_text_without_a_voice_line_says_so_when_asked_for_its_clip(voice_game):
    doc = _doc(voice_game)
    with pytest.raises(LookupError, match="no voice line"):
        doc.clip("-21", "target")


def test_2070_speech_is_found_by_the_text_id_and_its_speaker_is_the_folder(tmp_path):
    files = _write(
        tmp_path,
        {
            "data/loca/eng/txt/a.txt": _utf16("5=Hello\r\n6=Silent\r\n"),
            "data/loca/eng/speech/addon/eve/5.mp3": b"ID3fake",
            "data/loca/eng/speech/thor strindberg/5.mp3": b"ID3other",
        },
    )
    doc = TextDoc(files, LAYOUTS["anno2070"], EditFile("anno2070"), "english", "english")
    voice = doc.voice("5")
    assert voice.speaker in ("eve", "thor strindberg") and doc.voice("6") is None
    assert doc.clip("5", "source")[1] == ".mp3" and doc.rows(Filter(voiced=True)) == ["5"]
