"""The asset editor's icons (assetedit/icons.py), offscreen: the files an icon path can mean, a PNG and a DDS decoded,
and the tree rows that show the icon their asset names."""

from __future__ import annotations

import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from tests.fakes import asset_game

from annotoolbox.assetedit import icons
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.assettree import AssetTree


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def settle(tree) -> None:
    """Wait for the tree's background icon work and deliver its results."""
    tree._background.join()
    QApplication.processEvents()


def png_bytes(colour: str = "#ff0000", size: int = 4) -> bytes:
    image = QImage(size, size, QImage.Format.Format_RGBA8888)
    image.fill(QColor(colour))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def dds_bytes(width: int = 2, height: int = 2) -> bytes:
    """An uncompressed 32 bit DDS, blue in every texel (B, G, R, A in memory)."""
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<7I", header, 4, 124, 0x1007, height, width, width * 4, 0, 0)
    struct.pack_into("<2I4s5I", header, 76, 32, 0x41, b"\0\0\0\0", 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    return bytes(header) + bytes([255, 0, 0, 255]) * (width * height)


def test_an_icon_path_can_mean_the_file_the_texture_beside_it_or_a_dds():
    assert icons.candidates("data/ui/a/icon.png") == [
        "data/ui/a/icon.png", "data/ui/a/icon_0.png", "data/ui/a/icon_0.dds", "data/ui/a/icon.dds",
    ]  # fmt: skip
    assert icons.candidates("data/ui/icon")[0] == "data/ui/icon"
    shipped = icons.candidates("data/ui/fhd/base/icon_content/generic/icon_2d_hint.png")  # 117 ships it as 4k
    assert "data/ui/4k/base/icon_content/generic/icon_2d_hint_0.dds" in shipped
    assert "data/ui/2kimages/4k/base/icon_content/generic/icon_2d_hint_0.dds" in shipped


def test_a_png_and_a_dds_decode_and_other_bytes_do_not(qt):
    assert icons.decode(png_bytes()).width() == 4
    dds = icons.decode(dds_bytes())
    assert (dds.width(), dds.height()) == (2, 2) and dds.pixelColor(0, 0).blue() == 255
    assert icons.decode(b"not a picture") is None


def test_the_source_reads_each_file_once_and_finds_the_texture_beside_a_path(qt):
    reads = []
    files = {"data/ui/x_0.dds": dds_bytes()}

    def read(path):
        reads.append(path)
        return files[path]

    source = icons.IconSource(read)
    assert source.image("data/ui/x.png") is not None and source.image("data/ui/x.png") is not None
    assert reads.count("data/ui/x_0.dds") == 1 and source.image("data/ui/none.png") is None


def test_tree_rows_show_the_icon_their_asset_names(qt, tmp_path):
    game = open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache")
    doc = game.document()
    doc.set_value(2001, "Standard/IconFilename", "data/ui/hut.png")
    tree = AssetTree()
    tree.icons = icons.IconSource(lambda path: {"data/ui/hut.png": png_bytes()}[path])
    tree.set_doc(doc)
    tree.search.setText("2001")
    tree.fill()  # the search waits for the typing to stop; the icons are decoded off-thread
    settle(tree)
    assert not tree.tree.topLevelItem(0).icon(0).isNull()
    tree.search.setText("1001")
    tree.fill()
    settle(tree)
    assert tree.tree.topLevelItem(0).text(1) == "1001" and tree.tree.topLevelItem(0).icon(0).isNull()


ATLAS_FILES = {
    "data/config/gui/iconfilemap.xml": b"<IconFileMap><IconFile><IconFilename>data/ui/atlas.png</IconFilename>"
    b"<IconWidth>2</IconWidth><IconHeight>2</IconHeight><IconFileID>7</IconFileID></IconFile></IconFileMap>",
    "data/config/game/icons.xml": b"<Icons><i><GUID>1001</GUID><Icons><i><IconFileID>7</IconFileID>"
    b"<IconIndex>3</IconIndex></i></Icons></i><i><GUID>1002</GUID><Icons></Icons></i></Icons>",
}


def atlas_source(qt):
    """An atlas of 4 x 4 pixels, 2 x 2 tiles: the tile 3 (bottom right) is green, the others red."""
    image = QImage(4, 4, QImage.Format.Format_RGBA8888)
    image.fill(QColor("#ff0000"))
    for x in range(2, 4):
        for y in range(2, 4):
            image.setPixelColor(x, y, QColor("#00ff00"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    files = {**ATLAS_FILES, "data/ui/atlas_0.png": bytes(buffer.data())}
    source = icons.IconSource(lambda path: files[path])
    from annotoolbox.sources import icons2070

    source.use_atlases(*icons2070.load_icon_data(lambda p: files[p], lambda p: p in files))
    return source


def test_an_asset_without_an_icon_file_gets_its_tile_of_the_atlas(qt, tmp_path):
    game = open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache")
    doc = game.document()
    source = atlas_source(qt)
    assert source.key_of(doc, 1001) == "atlas:1001" and source.key_of(doc, 1002) == ""
    tile = source.image("atlas:1001")
    assert (tile.width(), tile.height()) == (2, 2) and tile.pixelColor(0, 0).green() == 255
    assert source.describe("atlas:1001") == "tile 3 of data/ui/atlas.png"
    assert not source.picture("atlas:1001").isNull()


def test_tiles_are_cut_row_major_and_outside_ones_are_none():
    from annotoolbox.sources import icons2070

    atlas = icons2070.IconAtlas(1, "a.png", 46, 46)
    assert icons2070.tile_box(atlas, 184, 92, 5) == (46, 46, 46, 46)
    assert icons2070.tile_box(atlas, 184, 92, 8) is None


def test_an_edited_icon_overrides_the_games_tile_and_is_forgotten_when_it_changes(qt, tmp_path):
    game = open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache")
    doc = game.document()
    source = atlas_source(qt)
    source.edits = doc.changes.icons
    assert source.editable and source.image("atlas:1001").pixelColor(0, 0).green() == 255
    doc.set_icon(1001, 7, 0)  # the red top left tile
    source.forget(1001)
    assert source.image("atlas:1001").pixelColor(0, 0).red() == 255
    assert source.key_of(doc, 1002) == ""
    assert doc.undo.undo()
    assert 1001 not in doc.changes.icons


ICONS_TEXT = (
    b"<Icons><i><GUID>-1</GUID>\r\n<Icons><i><IconFileID>30</IconFileID>\r\n<IconIndex>5</IconIndex>\r\n</i>\r\n"
    b"</Icons>\r\n</i>\r\n<i><GUID>2</GUID>\r\n<Icons></Icons>\r\n</i>\r\n<i><GUID>3</GUID>\r\n"
    b"<Icons><i><IconFileID>9</IconFileID>\r\n</i></Icons>\r\n</i>\r\n</Icons>"
)


def test_icons_xml_is_edited_in_its_own_text_for_each_kind_of_entry():
    from annotoolbox.sources import icons2070

    out = icons2070.set_icons(ICONS_TEXT, {-1: [(31, 6)], 2: [(8, 1)], 3: [(9, 4)], 40: [(7, 2)]})
    atlases_icons = icons2070.parse_icons(__import__("xml.etree.ElementTree", fromlist=["x"]).fromstring(out))
    assert {g: (r[0].file_id, r[0].index) for g, r in atlases_icons.items()} == {
        -1: (31, 6),
        2: (8, 1),
        3: (9, 4),
        40: (7, 2),
    }
    assert out.startswith(
        b"<Icons><i><GUID>-1</GUID>\r\n<Icons><i><IconFileID>31</IconFileID>\r\n<IconIndex>6</IconIndex>"
    )
    assert icons2070.set_icons(ICONS_TEXT, {-1: [(30, 5)]}) == ICONS_TEXT  # setting what is there changes nothing
    assert icons2070.has_entry(ICONS_TEXT, 3) and not icons2070.has_entry(ICONS_TEXT, 4)


def test_a_change_sets_icons_are_saved_and_written_to_the_games_icons_file(tmp_path):
    from annotoolbox.assetedit import changeset, native
    from test_archiveimport import old_game

    root = old_game(tmp_path / "game")
    for path, data in ATLAS_FILES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    doc = open_game("anno2070", root, tmp_path / "cache").document()
    assert native.render(doc) == {}
    doc.set_icon(1001, 7, 0)
    doc.set_icon(1002, 7, 2)
    out = native.render(doc)
    assert (
        set(out) == {"data/config/game/icons.xml"} and b"<IconIndex>0</IconIndex>" in out["data/config/game/icons.xml"]
    )
    again = changeset.ChangeSet.from_json(doc.changes.to_json())
    assert again.icons == {1001: [[7, 0]], 1002: [[7, 2]]}


@pytest.mark.parametrize(
    "key,variable,path",
    [
        ("anno2070", "ANNO2070_DIR", "data/config/game/icons.xml"),
        ("anno1404", "ANNO1404_DIR", "addondata/config/game/icons.xml"),
        ("anno1701", "ANNO1701_DIR", "data/config/game/icons.xml"),
    ],
)
def test_every_installed_icons_file_takes_an_edit_and_reads_back(key, variable, path, tmp_path):
    import os
    import xml.etree.ElementTree as ET
    from pathlib import Path

    from annotoolbox.sources import icons2070
    from annotoolbox.sources.gametree import open_game as open_tree

    if not os.environ.get(variable) or not Path(os.environ[variable]).is_dir():
        pytest.skip(f"{variable} is not set")
    data = open_tree(key, Path(os.environ[variable]), tmp_path).read(path)
    before = icons2070.parse_icons(ET.fromstring(data))
    guids = list(before)[:3]
    out = icons2070.set_icons(data, dict.fromkeys(guids, [(999, 77)]) | {2_000_000_001: [(998, 5)]})
    after = icons2070.parse_icons(ET.fromstring(out))
    assert all((after[g][0].file_id, after[g][0].index) == (999, 77) for g in guids)
    assert after[2_000_000_001][0].index == 5
    assert all(after[g][1:] == before[g][1:] for g in guids)  # the other icons of an entry stay
    assert {g: r for g, r in after.items() if g not in guids and g != 2_000_000_001} == {
        g: r for g, r in before.items() if g not in guids
    }


TWO_ICONS = (
    b"<Icons><i><GUID>5</GUID><Icons><i><IconFileID>7</IconFileID><IconIndex>1</IconIndex></i>"
    b"<i><IconFileID>7</IconFileID><IconIndex>2</IconIndex></i></Icons></i><i><GUID>6</GUID><Icons/></i></Icons>"
)


def test_every_icon_of_an_entry_is_read_and_set_by_its_place():
    import xml.etree.ElementTree as ET

    from annotoolbox.sources import icons2070

    def read(data):
        return {
            g: [(r.file_id, r.index) for r in refs] for g, refs in icons2070.parse_icons(ET.fromstring(data)).items()
        }

    assert read(TWO_ICONS) == {5: [(7, 1), (7, 2)]}
    second = icons2070.set_icons(TWO_ICONS, {5: [(), (9, 4)]})  # () leaves the first as it is
    assert read(second) == {5: [(7, 1), (9, 4)]}
    both = icons2070.set_icons(TWO_ICONS, {5: [(8, 0), (8, 3), (8, 9)]})  # the third is appended
    assert read(both) == {5: [(8, 0), (8, 3), (8, 9)]}
    assert read(icons2070.set_icons(TWO_ICONS, {6: [(7, 3)]})) == {5: [(7, 1), (7, 2)], 6: [(7, 3)]}
    assert icons2070.set_icons(TWO_ICONS, {5: [(7, 1), (7, 2)]}) == TWO_ICONS
    with pytest.raises(icons2070.IconsError):
        icons2070.set_icon(TWO_ICONS, 5, 1, 1, position=4)  # no gap: there is no icon 3 to come before it
    with pytest.raises(icons2070.IconsError):
        icons2070.set_icon(TWO_ICONS, 77, 1, 1, position=1)  # an entry the file lacks starts with its first icon


def test_a_second_icon_is_set_undone_saved_and_written(qt, tmp_path):
    from annotoolbox.assetedit import changeset, native
    from test_archiveimport import old_game

    root = old_game(tmp_path / "game")
    files = {**ATLAS_FILES, "data/config/game/icons.xml": TWO_ICONS.replace(b"<GUID>5<", b"<GUID>1001<")}
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    doc = open_game("anno2070", root, tmp_path / "cache").document()
    doc.set_icon(1001, 7, 3, position=1)
    assert doc.changes.icons == {1001: [[], [7, 3]]}
    out = native.render(doc)["data/config/game/icons.xml"]
    assert out.count(b"<IconIndex>3</IconIndex>") == 1 and b"<IconIndex>1</IconIndex>" in out  # the first stays
    assert changeset.ChangeSet.from_json(doc.changes.to_json()).icons == {1001: [[], [7, 3]]}
    assert doc.undo.undo() and doc.changes.icons == {}
    assert doc.undo.redo() and doc.changes.icons == {1001: [[], [7, 3]]}


def test_the_source_tells_the_icons_of_an_entry_apart(qt, tmp_path):
    source = atlas_source(qt)
    source.edits = {1001: [[], [7, 0]], 1002: [[7, 2]]}
    assert source.count_of(1001) == 2 and source.count_of(1002) == 1 and source.count_of(5) == 0
    assert (
        source.ref_of(1001, 0).index == 3 and source.ref_of(1001, 1).index == 0
    )  # the game's first, the edited second
    assert source.ref_of(1002).index == 2 and source.ref_of(1002, 1) is None
    assert source.image(icons.atlas_key(1001, 1)).pixelColor(0, 0).red() == 255
    assert source.describe(icons.atlas_key(1001, 1)) == "tile 0 of data/ui/atlas.png"
    source.forget(1001)
    assert icons.atlas_key(1001, 1) not in source._images


def test_the_icon_tab_edits_the_icon_it_shows(qt, tmp_path):
    from annotoolbox.assetedit.assettabs import IconPanel

    doc = open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache").document()
    source = atlas_source(qt)
    source.edits = doc.changes.icons
    panel = IconPanel(source)
    panel.present(doc, 1001)
    assert panel.number.maximum() == 1 and (panel.atlas.value(), panel.tile.value()) == (7, 3)
    panel.number.setValue(1)  # one past the last: a new icon
    panel.atlas.setValue(7)
    panel.tile.setValue(2)
    panel._set()
    assert doc.changes.icons == {1001: [[], [7, 2]]}
    panel.number.setValue(0)
    assert panel.tile.value() == 3


def test_a_tree_row_shows_an_edited_icon_at_once_without_a_new_fill(qt, tmp_path):
    doc = open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache").document()
    tree = AssetTree()
    tree.icons = source = atlas_source(qt)
    source.edits = doc.changes.icons
    tree.set_doc(doc)
    tree.search.setText("1001")
    tree.fill()
    settle(tree)
    row = tree.tree.topLevelItem(0)
    centre = lambda: row.icon(0).pixmap(28, 28).toImage().pixelColor(14, 14)  # noqa: E731
    assert centre().green() == 255  # the game's tile 3
    doc.set_icon(1001, 7, 0)  # the red tile
    tree.mark({1001})
    settle(tree)
    assert tree.tree.topLevelItem(0) is row and centre().red() == 255
