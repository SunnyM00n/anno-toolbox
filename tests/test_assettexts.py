"""The asset editor's texts for every game: the XML text files (117, 1800, 2205) and the `id=text` line files of 2070
and 1404, read per language and written whole by the native writer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.fakes import asset_game

from annotoolbox.assetedit import assetgame, native
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.gamefiles import LAYOUTS, GameFiles, folder_files
from annotoolbox.sources.gametree import loose_tree

LINES = "100=Old name\r\n# a comment\r\n101=Other\r\n"


def _lines_game(root, language="eng"):
    path = root / f"data/loca/{language}/txt/buildings.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe" + LINES.encode("utf-16-le"))
    other = root / f"data/loca/{language}/txt/addon/more.txt"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"\xff\xfe" + "300=In the add-on\r\n".encode("utf-16-le"))
    return folder_files(root, root / "cache")


def test_the_layouts_name_their_languages_and_text_files():
    assert LAYOUTS["anno2070"].language_code("english") == "eng" and LAYOUTS["anno2070"].text_format == "lines"
    assert LAYOUTS["anno1404"].language_names[:2] == ("english", "german")
    assert LAYOUTS["anno2205"].texts.endswith("texts_{language}.xml") and "polish" in LAYOUTS["anno2205"].language_names
    assert LAYOUTS["anno117"].language_code("german") == "german"


def test_line_texts_are_read_from_every_file_of_the_language(tmp_path):
    files = _lines_game(tmp_path)
    got = assetgame.read_texts(files, LAYOUTS["anno2070"], "english")
    assert got == {"100": "Old name", "101": "Other", "300": "In the add-on"}
    assert assetgame.read_texts(files, LAYOUTS["anno2070"], "german") == {}


def test_edited_lines_are_replaced_in_their_file_and_new_ids_go_into_a_file_of_the_mods_own(tmp_path):
    files = _lines_game(tmp_path)
    changes = SimpleNamespace(
        mod_id="my-mod", game="anno2070", texts={"english": {"101": "Changed", "999": "Brand new"}}
    )
    out = native.render_texts(SimpleNamespace(files=files, changes=changes))
    edited = out["data/loca/eng/txt/buildings.txt"]
    assert edited.startswith(b"\xff\xfe")
    assert edited[2:].decode("utf-16-le") == "100=Old name\r\n# a comment\r\n101=Changed\r\n"  # the rest as shipped
    fresh = out["data/loca/eng/txt/my-mod.txt"]
    assert fresh[2:].decode("utf-16-le") == "999=Brand new\r\n" and len(out) == 2


def test_xml_texts_are_replaced_in_place_and_new_ones_appended_for_117(tmp_path):
    game = open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache")
    doc = game.document()
    doc.set_text("english", "-1001", "A <big> hut")
    doc.set_text("english", "-5000", "Brand new")
    out = native.render_texts(doc)
    text = out["data/base/config/gui/texts_english.xml"].decode("utf-8")
    assert "<Text>A &lt;big&gt; hut</Text>" in text and "<LineId>-5000</LineId>" in text and "A small hut" not in text
    assert text.index("-1001") < text.index("-5000") < text.index("</Texts>")
    assert native.render_texts(game.document()) == {}


def test_the_asset_editors_game_files_are_the_core_game_tree_opened_late(tmp_path):
    root = tmp_path / "game"
    (root / "Data").mkdir(parents=True)
    (root / "Data" / "A.xml").write_bytes(b"<a/>")
    opened = []
    files = GameFiles(lambda: opened.append(1) or loose_tree(root), "stamp", tmp_path / "cache")
    assert not opened and files.stamp == "stamp"
    assert files.glob("DATA/*.xml") == ["data/a.xml"] and files.read("data/A.xml") == b"<a/>" and len(opened) == 1
    assert files.local("DATA/a.xml") == root / "Data" / "A.xml" and files.tree.exists("data/a.xml")
    with pytest.raises(FileNotFoundError):
        files.local("data/none.xml")


def test_an_empty_text_entry_is_replaced_not_followed_by_a_second_entry(tmp_path):
    path = tmp_path / "data/base/config/gui/texts_english.xml"
    path.parent.mkdir(parents=True)
    path.write_bytes(
        b"\xef\xbb\xbf<Texts>\r\n  <Text>\r\n    <LineId>-12</LineId>\r\n    <Text/>\r\n  </Text>\r\n</Texts>\r\n"
    )
    changes = SimpleNamespace(mod_id="m", game="anno117", texts={"english": {"-12": "Filled"}})
    out = native.render_texts(SimpleNamespace(files=folder_files(tmp_path, tmp_path / "cache"), changes=changes))
    text = out["data/base/config/gui/texts_english.xml"].decode("utf-8-sig")
    assert text.count("<LineId>-12</LineId>") == 1 and "<Text>Filled</Text>" in text and "<Text/>" not in text


def test_a_line_break_in_a_text_of_a_line_game_stays_inside_its_line(tmp_path):
    files = _lines_game(tmp_path)
    changes = SimpleNamespace(
        mod_id="my-mod", game="anno2070", texts={"english": {"101": "One\nTwo", "999": "New\r\nlines"}}
    )
    out = native.render_texts(SimpleNamespace(files=files, changes=changes))
    edited = out["data/loca/eng/txt/buildings.txt"][2:].decode("utf-16-le")
    assert "101=One<br/>Two\r\n" in edited and edited.count("\n") == LINES.count("\n")
    assert out["data/loca/eng/txt/my-mod.txt"][2:].decode("utf-16-le") == "999=New<br/>lines\r\n"
