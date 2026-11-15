"""The text editor's core on synthetic text files of every style: reading, writing back, the status of a row, the
checks, the glossary, the exchange formats and what a project's texts become as files."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from annotoolbox.assetedit.gamefiles import LAYOUTS, folder_files
from annotoolbox.suite.project import Project
from annotoolbox.textedit import checks, exchange, projectpart, render, textfiles, texttable
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
