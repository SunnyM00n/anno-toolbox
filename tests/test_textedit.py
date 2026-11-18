"""The text editor's core on synthetic text files of every style: reading, writing back, the status of a row, the
checks, the glossary, the exchange formats (odd CSV, XLIFF and PO input too), 117's twin lines and what a project's
texts become as files, alone and with the asset editor's."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from annotoolbox.assetedit import projectpart as assetpart
from annotoolbox.assetedit.changeset import ChangeSet
from annotoolbox.assetedit.gamefiles import LAYOUTS, folder_files
from annotoolbox.suite.project import Project
from annotoolbox.textedit import checks, exchange, merged, projectpart, render, textfiles, texttable
from annotoolbox.textedit.editfile import Edit, EditFile
from annotoolbox.textedit.glossary import Glossary, game_terms
from annotoolbox.textedit.textdoc import CHANGED, NEW, OUTDATED, TRANSLATED, UNTRANSLATED, Filter, TextDoc

XML_117 = (
    "<TextExport>\r\n  <Texts>\r\n"
    "    <Text>\r\n      <LineId>-10</LineId>\r\n      <Text>Save &amp; exit {0} &lt;b&gt;now&lt;/b&gt;</Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-11</LineId>\r\n      <Text>Two\r\nlines</Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-12</LineId>\r\n      <Text/>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-10</LineId>\r\n      <Text>Save &amp; exit {0} &lt;b&gt;now&lt;/b&gt;</Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-13</LineId>\r\n      <Text>Wood</Text>\r\n    </Text>\r\n"
    "  </Texts>\r\n</TextExport>"
)
GERMAN_117 = (
    "<TextExport>\r\n  <Texts>\r\n"
    "    <Text>\r\n      <LineId>-10</LineId>\r\n      <Text>Speichern &amp; beenden {0} &lt;b&gt;jetzt&lt;/b&gt;</Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-11</LineId>\r\n      <Text>Two\r\nlines</Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-12</LineId>\r\n      <Text></Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-13</LineId>\r\n      <Text>Holz</Text>\r\n    </Text>\r\n"
    "  </Texts>\r\n</TextExport>"
)
METADATA = (
    "<TextExport><Texts><Text><LineId>-10</LineId><Name>Menu Save Button</Name><FolderId>5</FolderId></Text>"
    "</Texts></TextExport>"
)
LINES = (
    '# header\r\n100=Old name\r\n\r\n101 = Other  \r\nISLAND_EDITOR=Editor\r\n102=\r\n<div class="x">credits</div>\r\n'
)


def _write(root, files: dict[str, bytes | str]):
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return folder_files(root, root / "cache")


def _utf16(text: str) -> bytes:
    return b"\xff\xfe" + text.encode("utf-16-le")


@pytest.fixture
def game117(tmp_path):
    gui = "data/base/config/gui/"
    return _write(tmp_path, {gui + "texts_english.xml": XML_117, gui + "texts_german.xml": GERMAN_117,
                             gui + "texts_metadata.xml": METADATA})  # fmt: skip


@pytest.fixture
def game2070(tmp_path):
    return _write(tmp_path, {
        "data/loca/eng/txt/interface.txt": _utf16(LINES),
        "data/loca/eng/txt/addon/more.txt": _utf16("300=In the add-on\r\n101=Overridden\r\n"),
        "data/loca/ger/txt/interface.txt": _utf16("100=Alter Name\r\n101=Anderes\r\n"),
    })  # fmt: skip


# the files
def test_an_xml_file_reads_with_its_escapes_and_breaks_and_writes_back_byte_for_byte():
    parsed = textfiles.parse("t.xml", XML_117.encode(), textfiles.XML)
    values = {s.key: s.value for s in parsed.slots}
    assert values == {"-10": "Save & exit {0} <b>now</b>", "-11": "Two\nlines", "-12": "", "-13": "Wood"}
    assert len(parsed.slots) == 5 and parsed.key_tag == "LineId"
    assert parsed.render() is parsed.raw
    assert parsed.render({s.key: s.value for s in parsed.slots}) == XML_117.encode()  # its own texts: nothing changes


def test_an_xml_file_replaces_every_slot_of_an_id_and_adds_before_the_end():
    parsed = textfiles.parse("t.xml", XML_117.encode(), textfiles.XML)
    out = parsed.render({"-10": "A < B & C", "-12": "now filled"}, {"-99": "New\nline"}).decode()
    assert out.count("A &lt; B &amp; C") == 2 and "<Text>now filled</Text>" in out
    assert "<LineId>-99</LineId>\r\n      <Text>New\r\nline</Text>" in out
    again = textfiles.parse("t.xml", out.encode(), textfiles.XML)
    assert {s.key: s.value for s in again.slots}["-99"] == "New\nline" and out.endswith("</Texts>\r\n</TextExport>")


def test_a_line_file_keeps_comments_blanks_symbolic_keys_and_the_rest_while_a_text_changes():
    raw = _utf16(LINES)
    parsed = textfiles.parse("t.txt", raw, textfiles.LINES)
    assert [(s.key, s.value) for s in parsed.slots] == [
        ("100", "Old name"), ("101", "Other"), ("ISLAND_EDITOR", "Editor"), ("102", ""),
    ]  # fmt: skip
    assert parsed.render({s.key: s.value for s in parsed.slots}) == raw
    out = parsed.render({"101": "Changed", "102": "Filled"}, {"500": "Added"})
    text = out[2:].decode("utf-16-le")
    assert text.startswith("# header\r\n100=Old name\r\n\r\n101 = Changed  \r\n") and "102=Filled\r\n" in text
    assert text.endswith("credits</div>\r\n500=Added\r\n") and out.startswith(b"\xff\xfe")


def test_a_file_without_a_signature_and_the_other_byte_order_keep_their_encoding():
    for raw in ("1=a\n".encode("utf-16-le"), b"\xfe\xff" + "1=a\n".encode("utf-16-be"), b"\xef\xbb\xbf1=a\n", b"1=a\n"):
        parsed = textfiles.parse("t.txt", raw, textfiles.LINES)
        assert parsed.render({"1": "b"}) == raw.replace(b"a", b"b".ljust(len(b"a")), 1) or textfiles.encode(
            "1=b\n", parsed.encoding
        ) == parsed.render({"1": "b"})
        assert parsed.render({"1": "a"}) == raw


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


# the tables
def test_languages_are_the_files_the_game_holds_and_a_double_key_follows_the_games_rule(game117, game2070, tmp_path):
    assert texttable.languages(game117, LAYOUTS["anno117"]) == {"english": "english", "german": "german"}
    assert texttable.languages(game2070, LAYOUTS["anno2070"]) == {"english": "eng", "german": "ger"}
    xml = texttable.read_language(
        game117, LAYOUTS["anno117"], "english", texttable.read_names(game117, LAYOUTS["anno117"])
    )
    assert (
        len(xml.table) == 5 and xml.table.get("-10").name == "Menu Save Button" and xml.table.get("-10").group == "Menu"
    )
    lines = texttable.read_language(game2070, LAYOUTS["anno2070"], "english")
    assert lines.table.get("101").text == "Other" and lines.table.get("101").group == "interface"  # a later file wins
    assert lines.table.get("300").file == "data/loca/eng/txt/addon/more.txt"


def test_2205s_master_and_frenglisch_files_are_no_languages(tmp_path):
    gui = "data/config/gui/"
    body = "<TextExport><Texts><Text><GUID>1</GUID><Text>x</Text></Text></Texts></TextExport>"
    files = _write(tmp_path, {gui + f"texts_{n}.xml": body for n in ("english", "german", "master", "frenglisch")})
    assert list(texttable.languages(files, LAYOUTS["anno2205"])) == ["english", "german"]
    assert "master" in texttable.languages(files, LAYOUTS["anno1800"])  # 1800 has no such list: every file is one


# the document
def _doc(files, layout="anno117", target="german"):
    return TextDoc(files, LAYOUTS[layout], EditFile(layout), "english", target)


def test_the_status_of_a_row_follows_the_target_the_source_and_the_edits(game117):
    doc = _doc(game117)
    assert doc.status("-10") == TRANSLATED and doc.status("-12") == UNTRANSLATED  # an empty text
    doc.edits.put("german", "-13", Edit("Holz", None))
    assert doc.status("-11") == UNTRANSLATED  # the very same text as the source
    assert doc.edit("-11", "Zwei\nZeilen") == CHANGED and doc.target_text("-11") == "Zwei\nZeilen"
    doc.edit("-10", "Neu {0} <b>x</b>", "german")
    assert doc.edits.get("german", "-10").source == "Save & exit {0} <b>now</b>"
    assert doc.edit("-10", "Neu {0} <b>x</b>") == CHANGED
    doc.edit("-10", "Save & exit {0} <b>later</b>", "english")  # the source changed after the translation
    assert doc.status("-10") == OUTDATED
    assert doc.edit("-77", "Brand new", "english") == NEW and doc.status("-77") == NEW and "-77" in doc.ids()


def test_an_edit_equal_to_the_games_text_is_no_edit_and_undo_and_redo_walk_the_steps(game117):
    doc = _doc(game117)
    doc.edit("-13", "Holzstamm")
    doc.edit("-13", "Holz")  # back to the game's text
    assert doc.edits.get("german", "-13") is None and doc.status("-13") == TRANSLATED
    assert doc.undo() and doc.edits.get("german", "-13").text == "Holzstamm"
    assert doc.undo() and doc.edits.count() == 0 and not doc.undo()
    assert doc.redo() and doc.redo() and doc.edits.count() == 0 and not doc.redo()
    doc.edit("-13", "Holzstamm")
    assert doc.revert("-13") == TRANSLATED and doc.edits.count() == 0


def test_the_rows_are_filtered_by_status_file_group_words_and_problems(game117, game2070):
    doc = _doc(game117)
    assert doc.rows(Filter(statuses=frozenset({UNTRANSLATED}))) == ["-11", "-12"]
    assert doc.rows(Filter(group="Menu")) == ["-10"] and doc.rows(Filter(query="holz wood")) == ["-13"]
    assert doc.rows(Filter(query="menu save")) == ["-10"]  # the editor name
    doc.edit("-10", "Speichern")  # the placeholder and the tags are gone
    assert doc.rows(Filter(only_problems=True)) == ["-10"]
    assert [p.kind for p in doc.problems("-10") if p.severity == checks.ERROR] == ["placeholder", "markup", "markup"]
    old = _doc(game2070, "anno2070")
    assert old.files_listed() == ["data/loca/eng/txt/addon/more.txt", "data/loca/eng/txt/interface.txt"]
    assert old.rows(Filter(file="data/loca/eng/txt/addon/more.txt")) == ["300"]
    assert old.counts()[TRANSLATED] == 2  # 100 and 101 have a German text the source does not equal


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


# the checks
def test_placeholders_markup_and_breaks_must_survive_a_translation():
    ok = checks.check(
        "Hello {Text:GetText(5)} [Tag x] <b>bold</b><br/>end", "Hallo {Text:GetText(5)} [Tag x] <b>fett</b><br />Ende"
    )
    assert ok == []
    kinds = {(p.severity, p.kind) for p in checks.check("A {0} <b>b</b>", "A {1} <b>b")}
    assert kinds == {("error", "placeholder"), ("error", "markup")}
    assert [p.kind for p in checks.check("A", "A {").__iter__()] == ["braces"]
    assert (
        checks.check("a\nb", "a b")[0].kind == "breaks"
        and checks.check("a<br/>b", "a<br/>b<br/>c")[0].severity == "warning"
    )
    assert checks.check("a", "a\nb", one_line=True)[0].kind == "break"
    assert checks.check("Short", "A very much longer text than the source")[0].kind == "length"
    assert checks.check("Text", "")[0].kind == "length" and checks.check("", "") == []
    assert checks.check("a </>", "b </>") == [] and checks.unbalanced("<b>x") == ["<b>"]
    assert checks.check("[GUIDNAME 5] stays", "[GUIDNAME 6] bleibt")[0].kind == "placeholder"
    assert checks.check("<b>open", "<b>offen") == []  # the shipped text is as unbalanced as the translation


def test_the_glossary_finds_the_games_terms_and_says_which_a_target_lacks(game117):
    doc = _doc(game117)
    assert game_terms(doc.table("english"), doc.table("german")) == {"Wood": "Holz"}
    glossary = Glossary({"Wood": "Holz", "Tax Office": "Steueramt"}, {"Senate": "Senat"})
    assert glossary.terms_in("The Tax Office and the Senate need Wood, not wood.") == ["Tax Office", "Senate", "Wood"]
    assert glossary.hints("Need Wood", "Brauche Stein") and not glossary.hints("Need Wood", "Brauche Holz")
    doc.edits.glossary["german"] = {"Wood": "Brennholz"}
    assert doc.glossary().translation("Wood") == "Brennholz" and doc.hints("-13") == [
        "Wood is Brennholz in the glossary"
    ]


# the exchange formats
@pytest.mark.parametrize("fmt", exchange.FORMATS)
def test_a_translation_goes_out_and_comes_back_in_every_format(game117, fmt):
    doc = _doc(game117)
    doc.edit("-11", 'Zwei "Zeilen"\nmit \\ und, Komma')
    rows = exchange.rows_of(doc, doc.ids())
    data = exchange.write(fmt, rows, "anno117", "english", "german")
    back = exchange.read(fmt, data)
    mine = {r.id: r for r in back}
    assert mine["-11"].target == 'Zwei "Zeilen"\nmit \\ und, Komma' and mine["-11"].source == "Two\nlines"
    assert mine["-10"].target == doc.target_text("-10") and mine["-10"].source == "Save & exit {0} <b>now</b>"


def test_an_import_applies_rows_as_one_undo_step_and_an_old_source_makes_them_outdated(game117):
    doc = _doc(game117)
    rows = [
        exchange.Row("-13", "Wood", "Holzscheit"),
        exchange.Row("-11", "An older source", "Zwei Zeilen"),
        exchange.Row("-12", "", ""),
        exchange.Row("-10", "", doc.target_text("-10")),
        exchange.Row("-555", "Ghost", "Geist"),
    ]
    result = exchange.apply(doc, rows)
    assert (result.applied, result.unchanged, result.empty, result.unknown) == (2, 1, 1, ["-555"])
    assert doc.status("-13") == CHANGED and doc.status("-11") == OUTDATED
    assert doc.undo() == "import 2 texts" and doc.edits.count() == 0
    added = exchange.apply(doc, [exchange.Row("-555", "Ghost", "Geist")], add_new=True)
    assert added.added == 1 and doc.status("-555") == NEW and doc.text("english", "-555") == "Ghost"
    assert "unknown ids" in added.line()
    assert exchange.format_of("x.XLF") == "xliff" and pytest.raises(ValueError, exchange.format_of, "x.txt")


def test_the_edit_file_round_trips_and_a_file_of_another_format_is_refused(tmp_path):
    edits = EditFile(
        "anno117", "english", {"german": {"-10": Edit("a\nb", "src"), "-11": Edit("c")}}, {"german": {"Wood": "Holz"}}
    )
    path = tmp_path / "text-edits" / "anno117.json"
    edits.save(path)
    assert (
        EditFile.load(path) == edits and edits.count() == 2 and EditFile.load(tmp_path / "none.json", "x").game == "x"
    )
    path.write_text(json.dumps({"format": 9}), encoding="utf-8")
    with pytest.raises(ValueError, match="format 9"):
        EditFile.load(path)


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


# what the project writes
def test_117_gets_a_modops_per_language_and_a_new_id_in_every_language(game117):
    edits = EditFile(
        "anno117", "english", {"german": {"-13": Edit("Holzstamm", "Wood")}, "english": {"-99": Edit("Brand new")}}
    )
    out = render.render(game117, LAYOUTS["anno117"], edits, "my-mod", lambda _s: None)
    assert sorted(out) == ["data/base/config/gui/texts_english.xml", "data/base/config/gui/texts_german.xml"]
    german = out["data/base/config/gui/texts_german.xml"].decode()
    assert "Text[LineId='-13']/Text" in german and "Holzstamm" in german and "<LineId>-99</LineId>" in german
    assert "Brand new" in german  # the source's text where the language has none
    assert "-13" not in out["data/base/config/gui/texts_english.xml"].decode()


def test_2070_gets_whole_files_the_edited_lines_changed_and_new_ids_in_a_file_of_the_mods_own(game2070):
    edits = EditFile(
        "anno2070", "english", {"english": {"100": Edit("Renamed"), "300": Edit("Add-on text"), "900": Edit("New")}}
    )
    out = render.render(game2070, LAYOUTS["anno2070"], edits, "my-mod", lambda _s: None)
    assert sorted(out) == [
        "data/loca/eng/txt/addon/more.txt", "data/loca/eng/txt/interface.txt", "data/loca/eng/txt/my-mod.txt",
        "data/loca/ger/txt/my-mod.txt",
    ]  # fmt: skip
    assert "100=Renamed\r\n" in out["data/loca/eng/txt/interface.txt"][2:].decode("utf-16-le")
    assert out["data/loca/eng/txt/addon/more.txt"][2:].decode("utf-16-le").startswith("300=Add-on text\r\n")
    assert (
        out["data/loca/ger/txt/my-mod.txt"][2:].decode("utf-16-le") == "900=New\r\n"
    )  # German has none: the source's text


def test_2205_gets_its_language_file_whole(tmp_path):
    body = "<TextExport>\r\n  <Texts>\r\n    <Text>\r\n      <GUID>1</GUID>\r\n      <Text>x</Text>\r\n    </Text>\r\n  </Texts>\r\n</TextExport>"
    files = _write(tmp_path, {"data/config/gui/texts_english.xml": body})
    edits = EditFile("anno2205", "english", {"english": {"1": Edit("y"), "2": Edit("z")}})
    out = render.render(files, LAYOUTS["anno2205"], edits, "m", lambda _s: None)
    text = out["data/config/gui/texts_english.xml"].decode()
    assert "<Text>y</Text>" in text and "<GUID>2</GUID>" in text and list(out) == ["data/config/gui/texts_english.xml"]


def test_the_project_part_writes_what_the_edits_file_holds(game117, tmp_path):
    project = Project(tmp_path / "proj", mod_id="my-mod", game="anno117")
    assert not projectpart.has_work(project)
    EditFile("anno117", "english", {"german": {"-13": Edit("Holzstamm", "Wood")}}).save(projectpart.edits_path(project))
    assert projectpart.has_work(project)
    install = tmp_path / "game"
    (install / "data/base/config/gui").mkdir(parents=True)
    (install / "data/base/config/gui/texts_english.xml").write_text(XML_117, encoding="utf-8")
    (install / "data/base/config/gui/texts_german.xml").write_text(GERMAN_117, encoding="utf-8")
    settings = SimpleNamespace(anno117=str(install), workspace=str(tmp_path / "ws"))
    logged: list[str] = []
    out = projectpart.render(project, settings, logged.append)
    assert "Holzstamm" in out["data/base/config/gui/texts_german.xml"].decode() and logged[-1].startswith(
        "texts: 1 texts"
    )


def test_a_new_id_of_a_line_game_is_one_line_in_the_mods_own_file(tmp_path):
    files = _write(tmp_path, {"data/loca/eng/txt/a.txt": _utf16("1=a\r\n")})
    edits = EditFile("anno2070", "english", {"english": {"9": Edit("two\nlines")}})
    out = render.render(files, LAYOUTS["anno2070"], edits, "my-mod", lambda _s: None)
    assert out["data/loca/eng/txt/my-mod.txt"][2:].decode("utf-16-le") == "9=two<br/>lines\r\n"


# one writer for both apps (docs/text-editor.md, that section)
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
