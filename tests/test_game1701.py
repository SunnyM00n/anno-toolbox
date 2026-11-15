"""Anno 1701 in the asset editor (assetedit/game1701.py, sources/props1701.py): the objects of ``property.ini``, the
balancing table and the game values read, edited and written back; a synthetic game, and the install where set."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from annotoolbox.assetedit import game1701, native
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.sources import props1701

PROPS = (
    "[OBJECT]\r\nGUID 1055\r\nNAME Twister\r\nVALUEID 100 6 1 512\r\nVALUEID 101 1 1 Twister()\r\n"
    "[PROPERTY]\r\nPROPID 1\r\nVALUEID 20001 1 1 2\r\nVALUEID 20002 1 1 data/twister.xml\r\n"
    "[OBJECT]\r\nGUID 3150\r\nNAME clay_earth\r\nVALUEID 100 6 1 256\r\nVALUEID 101 1 1 \r\n"
    "[OBJECT]\r\nGUID 4000\r\nNAME Hut\r\nVALUEID 100 6 1 8\r\n"
    "[PROPERTY]\r\nPROPID 7\r\nVALUEID 80001 6 1 1048576\r\nVALUEID 80002 1 1 0\r\n"
)
TABLE = "GUID\tName\tTrefferpunkte\tEinflußradius\r\n0\tStraße\t\t\r\n4000\tHütte\t100\t7,5\r\n5000\tOnly here\t5\t\r\n"
VALUES = (
    "<GameValues>\r\n  <NeedLevels>\r\n    <Pioneer>\r\n      <!-- a comment ending wrong--->\r\n"
    "      <PioneerNeedsDraperyCount>48</PioneerNeedsDraperyCount>\r\n    </Pioneer>\r\n  </NeedLevels>\r\n"
    "  <Speed>2.5</Speed>\r\n</GameValues>\r\n"
)
ICONS = (
    "<Icons><i><GUID>4000</GUID><Icons><i><IconFileID>63</IconFileID><IconIndex>1</IconIndex></i></Icons></i></Icons>"
)
ICON_MAP = (
    "<IconFileMap><IconFile><IconFilename>data/ui/icons.tga</IconFilename><IconWidth>2</IconWidth>"
    "<IconHeight>2</IconHeight><IconFileID>63</IconFileID></IconFile></IconFileMap>"
)
TEXTS = "1055=Tornado\r\n4000=Hut text\r\n"


def make_game(root: Path) -> Path:
    files = {
        "data/config/game/property.ini": PROPS.encode("latin-1"),
        "data/project/balancing/balancingtabelle.txt": TABLE.encode("latin-1"),
        "data/config/game/gamevalues.xml": VALUES.encode("latin-1"),
        "data/config/game/icons.xml": ICONS.encode(),
        "data/config/gui/iconfilemap.xml": ICON_MAP.encode(),
        "data/loca/eng/txt/guids.txt": b"\xff\xfe" + TEXTS.encode("utf-16-le"),
    }
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    return root


@pytest.fixture
def game(tmp_path):
    return open_game("anno1701", make_game(tmp_path / "game"), tmp_path / "cache")


def test_the_three_files_read_and_write_back_as_they_are():
    props = props1701.parse_props(PROPS.encode("latin-1"))
    assert [o.guid for o in props.objects] == [1055, 3150, 4000] and props.objects[1].values[1].text == ""
    assert props.objects[2].blocks[0].prop == 7 and props.value_types()[100] == 6
    assert props1701.render_props(props) == PROPS.encode("latin-1")
    table = props1701.parse_table(TABLE.encode("latin-1"))
    assert table.rows[4000] == ["4000", "Hütte", "100", "7,5"] and props1701.render_table(table) == TABLE.encode(
        "latin-1"
    )
    values = props1701.game_values(VALUES.encode("latin-1"))
    assert values == {("NeedLevels", "Pioneer", "PioneerNeedsDraperyCount"): "48", ("Speed",): "2.5"}
    changed = props1701.set_game_values(VALUES.encode("latin-1"), {("Speed",): "3"})
    assert changed == VALUES.replace("2.5", "3").encode("latin-1")
    with pytest.raises(ValueError, match="no single setting"):
        props1701.set_game_values(VALUES.encode("latin-1"), {("Nothing",): "1"})
    with pytest.raises(ValueError, match="line 1"):
        props1701.parse_props(b"VALUEID 1 1 1 x\r\n")


def test_the_game_opens_with_an_asset_per_object_row_and_the_settings(game):
    doc = game.document()
    assert {e.guid for e in game.index.entries} == {1055, 3150, 4000, 0, 5000, -1}
    assert doc.name(1055) == "Twister" and doc.template(4000) == "Object P7" and doc.template(0) == "Balancing only"
    assert doc.layers(4000).lookup(("P7", "V80001")).text == "1048576"
    assert doc.layers(4000).lookup(("Balancing", "Trefferpunkte")).text == "100"
    assert doc.layers(4000).lookup(("Balancing", "Einflussradius")).text == "7,5"  # the header's umlaut spelled out
    assert game.texts("english")["1055"] == "Tornado" and game.layout.virtual
    assert game.icon_data[1][4000][0].index == 1  # the table of icons.xml


def test_an_unchanged_game_writes_no_file_and_an_edit_writes_its_lines_only(game):
    doc = game.document()
    assert native.render(doc) == {}
    doc.set_value(1055, "Object/V100", "1024")
    doc.set_value(4000, "Balancing/Trefferpunkte", "120")
    doc.set_value(-1, "NeedLevels/Pioneer/PioneerNeedsDraperyCount", "50")
    out = native.render(doc)
    assert set(out) == {game1701.PROPS, game1701.TABLE, game1701.VALUES}
    assert out[game1701.PROPS] == PROPS.replace("VALUEID 100 6 1 512", "VALUEID 100 6 1 1024").encode("latin-1")
    assert out[game1701.TABLE] == TABLE.replace("100\t7,5", "120\t7,5").encode("latin-1")
    assert out[game1701.VALUES] == VALUES.replace("48", "50").encode("latin-1")


def test_new_changed_and_removed_assets_become_objects_and_rows(game):
    doc = game.document()
    doc.set_text("english", "4000", "A hut")
    doc.remove_asset(3150)
    made = doc.new_asset("Object P7", "Barn", anchor=4000)
    doc.set_value(made, "Object/V100", "16")
    out = native.render(doc)
    props = props1701.parse_props(out[game1701.PROPS])
    assert [o.guid for o in props.objects] == [1055, 4000, made]  # the barn after the hut it was made beside
    assert props.objects[2].name == "Barn" and props.objects[2].values[0].text == "16"
    assert any(path.endswith("txt") or "loca" in path for path in out)  # the text goes to a file of the language


def test_the_asset_window_shows_the_forms_and_icons_of_1701(game, tmp_path, qt_app=None):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from annotoolbox.assetedit.assetwindow import AssetWindow
    from annotoolbox.suite.settings import Settings

    settings = Settings(workspace=str(tmp_path / "ws"), anno1701=str(tmp_path / "game"))
    window = AssetWindow(settings, tmp_path / "settings.json")
    window.open("anno1701", wait=True)
    window.tree.select(4000)
    assert window.guid == 4000 and "Object" in window.form.blocks and "Balancing" in window.form.blocks
    assert window.icon_source.key_of(window.doc, 4000) == "atlas:4000"
    window.tree.select(1055)
    rows = window.texts.table
    window.texts.language.setCurrentText("english")
    window.texts.present(window.doc, 1055)
    assert (
        rows.rowCount() == 1 and rows.item(0, 2).text() == "Tornado"
    )  # the object's name text is the text of its GUID


# the install


def test_the_installed_files_write_back_byte_for_byte_and_open_as_assets(tmp_path):
    folder = os.environ.get("ANNO1701_DIR")
    if not folder or not Path(folder).is_dir():
        pytest.skip("ANNO1701_DIR is not set")
    game = open_game("anno1701", Path(folder), tmp_path)
    sources = game.files.sources
    tree = game.files.tree
    assert props1701.render_props(sources.props) == tree.read(game1701.PROPS)
    assert props1701.render_table(sources.table) == tree.read(game1701.TABLE)
    rows_only = set(sources.table.rows) - set(sources.props.by_guid())
    assert len(game.index.by_guid) == len(sources.props.objects) + len(rows_only) + 1  # and the settings
    doc = game.document()
    assert doc.name(1055) == "Twister" and native.render(doc) == {}
    doc.set_value(1055, "Object/V100", "1024")
    out = native.render(doc)
    original = tree.read(game1701.PROPS).decode("latin-1").split("\r\n")
    changed = [a for a, b in zip(original, out[game1701.PROPS].decode("latin-1").split("\r\n"), strict=True) if a != b]
    assert changed == ["VALUEID 100 6 1 512"]
    assert game.texts("english")["1055"] == "Tornado"


def test_a_setting_with_markup_characters_is_written_escaped_and_the_others_stay(tmp_path):
    root = make_game(tmp_path / "game")
    values = root / "data/config/game/gamevalues.xml"
    values.write_bytes(VALUES.replace("<Speed>", "<Note>a &amp; b</Note>\r\n  <Speed>").encode("latin-1"))
    doc = open_game("anno1701", root, tmp_path / "cache").document()
    doc.set_value(-1, "Speed", "3 < 4 & 5")
    text = native.render(doc)[game1701.VALUES].decode("latin-1")
    assert "<Note>a &amp; b</Note>" in text and "<Speed>3 &lt; 4 &amp; 5</Speed>" in text
