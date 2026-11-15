"""The old interface (guiold): dialogs, templates, backgrounds and texts of 2070, 1404 and 1701 on synthetic files,
the placing of a dialog, the tiles of a background, the page offscreen; and, with the games installed, every dialog."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from annotoolbox.guiold import files, richtext, scene
from annotoolbox.sources import gametree

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
GUI = "data/config/gui/"
DIALOG = """<FRAME><PATHID>TEST</PATHID><BACKGROUND>data/config/gui/backgrounds/frame.bkg</BACKGROUND>
<EXTENT><LEFT>10</LEFT><TOP>20</TOP><WIDTH>400</WIDTH><HEIGHT>300</HEIGHT></EXTENT><VISIBLE>0</VISIBLE>
<BUTTON><PATHID>OK</PATHID><TEMPLATE_REF_ID>BTN</TEMPLATE_REF_ID><EXTENT><LEFT>2</LEFT><TOP>3</TOP></EXTENT>
<TEXTSETTING><TEXTGUID>5</TEXTGUID></TEXTSETTING></BUTTON>
<PANEL><PATHID>HIDDEN</PATHID><VISIBLE>0</VISIBLE><EXTENT><WIDTH>9</WIDTH><HEIGHT>9</HEIGHT></EXTENT></PANEL>
<BOXLAYOUT><PATHID>BOX</PATHID><XSPACE>4</XSPACE><YSPACE>6</YSPACE><LINECOLUMNCOUNT>2</LINECOLUMNCOUNT>
<EXTENT><LEFT>0</LEFT><TOP>100</TOP><WIDTH>200</WIDTH><HEIGHT>100</HEIGHT></EXTENT>
<PANEL><PATHID>A</PATHID><EXTENT><WIDTH>30</WIDTH><HEIGHT>10</HEIGHT></EXTENT></PANEL>
<PANEL><PATHID>B</PATHID><EXTENT><WIDTH>20</WIDTH><HEIGHT>12</HEIGHT></EXTENT></PANEL>
<PANEL><PATHID>C</PATHID><EXTENT><WIDTH>20</WIDTH><HEIGHT>8</HEIGHT></EXTENT></PANEL></BOXLAYOUT>
<CARDLAYOUT><PATHID>CARDS</PATHID><EXTENT><LEFT>300</LEFT><WIDTH>50</WIDTH><HEIGHT>50</HEIGHT></EXTENT>
<PANEL><PATHID>P1</PATHID></PANEL><PANEL><PATHID>P2</PATHID></PANEL></CARDLAYOUT></FRAME>"""
TEMPLATE = "<TEMPLATE><PATHID>BTN</PATHID><EXTENT><WIDTH>100</WIDTH><HEIGHT>20</HEIGHT></EXTENT></TEMPLATE>"


def _bkg(columns: int, rows: int, atlas: str = "data/graphics/ui/frame.png") -> str:
    """A state of ``columns`` x ``rows`` 8 x 8 tiles of the atlas; the middle row and column stretch."""
    cells = []
    for r in range(rows):
        for c in range(columns):
            flags = (4 if 0 < c < columns - 1 else 0) | (64 if 0 < r < rows - 1 else 0)
            cells.append(
                f"<i><LocalFileID>0</LocalFileID><tileX>{c * 8}</tileX><tileY>{r * 8}</tileY><tileWidth>8</tileWidth>"
                f"<tileHeight>8</tileHeight><flags>{flags}</flags><Width>0</Width><Height>0</Height></i>"
            )
    return (
        f"<BackgroundLayout><m_localIconFileMap><i>{atlas}</i></m_localIconFileMap><BackgroundInfo><i>"
        f"<LayoutName>Normal</LayoutName><flags>0</flags><m_XSize>{columns}</m_XSize><m_YSize>{rows}</m_YSize>"
        "<m_Color_H>0</m_Color_H><m_Color_S>1</m_Color_S><m_Color_V>1</m_Color_V><m_Color_A>1</m_Color_A>"
        "<ClientAreaOffsets><left>5</left><top>6</top><right>5</right><bottom>4</bottom></ClientAreaOffsets>"
        f"<m_StencilIconGUID>-1</m_StencilIconGUID><IconList>{''.join(cells)}</IconList></i></BackgroundInfo>"
        "</BackgroundLayout>"
    )


BG2 = (  # 1701's spelling: arrays as i0000, i0001 ...
    "<BackgroundDefinitions><arraysize>1</arraysize><i0000><LayoutName>EmptyLayout</LayoutName><flags>0</flags>"
    "<m_XSize>1</m_XSize><m_YSize>1</m_YSize><m_Color>-1</m_Color><ClientAreaOffsets><left>0</left><top>0</top>"
    "<right>0</right><bottom>0</bottom></ClientAreaOffsets><IconList><arraysize>2</arraysize>"
    "<i0000><FileID>7</FileID><IconIndex>5</IconIndex><flags>0</flags><Width>0</Width><Height>0</Height></i0000>"
    "<i0001><FileID>1023</FileID><IconIndex>65535</IconIndex><flags>32</flags><Width>0</Width><Height>0</Height></i0001>"
    "</IconList></i0000></BackgroundDefinitions>"
)
ICON_MAP = (
    "<IconFileMap><IconFile><IconFileID>7</IconFileID><IconFilename>data/graphics/ui/icons.tga</IconFilename>"
    "<IconWidth>16</IconWidth><IconHeight>16</IconHeight><IsGameIcon>1</IsGameIcon></IconFile></IconFileMap>"
)


@pytest.fixture
def tree(tmp_path: Path):
    root = tmp_path / "game"
    for name, data in {
        "menudefinitions/test.dlg": DIALOG,
        "menudefinitions/__custom_windows.dlg": "<FRAME><PATHID>X</PATHID></FRAME>",
        "menudefinitions/button.tpl": TEMPLATE,
        "backgrounds/frame.bkg": _bkg(3, 3),
        "bg2/icon.bg2": BG2,
        "iconfilemap.xml": ICON_MAP,
    }.items():
        path = root / GUI / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
    texts = root / "data/loca/eng/txt/gui.txt"
    texts.parent.mkdir(parents=True)
    texts.write_bytes("# the dialog's texts\n5=Start\n".encode("utf-16"))
    return gametree.loose_tree(root)


def test_the_files_are_listed_by_kind_and_texts_are_found_by_id(tree):
    old = files.OldGui(tree)
    assert old.dialogs() == [GUI + "menudefinitions/test.dlg"]  # the library of components is not a window
    assert {e.kind for e in old.entries.values()} == {"dialog", "template", "background"}
    assert old.text("5") == "Start" and old.text(-1) == "" and old.text("99") == "#99"


def test_a_dialog_is_placed_from_its_templates_in_its_clients_area(tree):
    old = files.OldGui(tree)
    root = scene.build(old, old.dialog(GUI + "menudefinitions/test.dlg"))
    assert root.visible and root.rect == (10.0, 20.0, 400.0, 300.0)  # a frame is shown when the game opens it
    ok = next(n for n in root.walk() if n.name == "OK")
    assert ok.rect == (10 + 5 + 2, 20 + 6 + 3, 100.0, 20.0)  # the template's size, inside the frame's client area
    assert ok.text == "Start" and root.look is not None and root.look.offsets == (5, 6, 5, 4)
    assert not next(n for n in root.walk() if n.name == "HIDDEN").visible
    assert root.at(20, 40) is ok and root.at(1, 1) is None


def test_a_box_layout_stacks_its_children_and_a_card_layout_shows_one_page(tree):
    old = files.OldGui(tree)
    root = scene.build(old, old.dialog(GUI + "menudefinitions/test.dlg"))
    box = {n.name: n.rect for n in root.walk() if n.name in "ABC"}
    cx, cy = 10 + 5, 20 + 6 + 100
    assert box["A"][:2] == (cx, cy) and box["B"][:2] == (cx + 30 + 4, cy)  # two to a line, 4 apart
    assert box["C"][:2] == (cx, cy + 12 + 6)  # the next line below the taller of the first
    pages = {n.name: n.visible for n in root.walk() if n.name in ("P1", "P2")}
    assert pages == {"P1": True, "P2": False}


def test_a_nine_slice_keeps_its_corners_and_shares_the_rest_among_the_stretched_cells(tree):
    look = files.OldGui(tree).background(GUI + "backgrounds/frame.bkg")["Normal"]
    cells = {(t.x // 8, t.y // 8): r for t, r in scene.tile_rects(look, 100.0, 60.0)}
    assert cells[0, 0] == (0.0, 0.0, 8.0, 8.0) and cells[2, 2] == (92.0, 52.0, 8.0, 8.0)  # corners at their size
    assert cells[1, 1] == (8.0, 8.0, 84.0, 44.0)  # the middle takes the rest
    assert sum(r[2] * r[3] for r in cells.values()) == 100.0 * 60.0


def test_a_bg2_names_cells_of_an_atlas_by_number(tree):
    state = files.OldGui(tree).background("data\\config\\gui\\bg2\\icon.bg2")["EmptyLayout"]
    real, empty = state.tiles
    assert (real.atlas, real.x, real.y, real.w, real.h) == ("data/graphics/ui/icons.tga", 5, -1, 16, 16)
    assert empty.atlas == "" and files.stem("Data\\X\\Y.TGA") == "data/x/y"


def test_the_page_draws_a_dialog_and_inspects_the_widget_picked(tree):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.guiold.view import OldGuiPage

    qt = QApplication.instance() or QApplication([])
    page = OldGuiPage(tree)
    page.resize(1200, 700)
    page.sync_open = True
    page.open(GUI + "menudefinitions/test.dlg")
    ok = next(n for n in page.root.walk() if n.name == "OK")
    page.inspect(ok)
    rows = {page.inspector.topLevelItem(i).text(0): page.inspector.topLevelItem(i).text(1) for i in range(8)}
    assert rows["Name"] == "OK" and rows["Text"] == "Start"
    page.show()
    qt.processEvents()
    assert not page.stage.grab().isNull()
    page.close()


@pytest.mark.parametrize(("game", "variable"), [("anno2070", "ANNO2070_DIR"), ("anno1404", "ANNO1404_DIR"),
                                                ("anno1701", "ANNO1701_DIR")])  # fmt: skip
def test_every_dialog_of_the_installed_game_is_placed_with_its_backgrounds(game, variable):
    folder = os.environ.get(variable)
    if not folder:
        pytest.skip(f"{variable} is not set")
    old = files.OldGui(gametree.open_game(game, Path(folder)))
    dialogs = old.dialogs()
    assert len(dialogs) > 50
    looks = widgets = icons = hovers = held = styled = 0
    for path in dialogs:
        root = scene.build(old, old.dialog(path), "Normal+MouseOver")
        nodes = list(root.walk())
        widgets += len(nodes)
        looks += sum(1 for n in nodes if n.look is not None)
        icons += sum(1 for n in nodes if n.icon is not None)
        hovers += sum(1 for n in nodes if n.look is not None and n.look.name == "Normal+MouseOver")
        held += sum(len(n.children) for n in nodes if n.kind == "INSTANCE")
        styled += sum(1 for n in nodes if n.style is not None)
        wide = scene.build(old, old.dialog(path), screen=scene.SCREENS["16:9"])
        assert len(list(wide.walk())) == len(nodes) and wide.rect[2] <= scene.SCREENS["16:9"][0] + 2000
    assert widgets > 1000 and looks > widgets // 20  # the pictures are found for a good share of the widgets
    assert icons > 50 and hovers > 20  # the icons of icons.xml, the hover states of the buttons
    assert (held > 1000 and styled > 500) if game == "anno2070" else not (held or styled)  # components, the sheet
    for text in old._texts.values() if game == "anno2070" else ():
        richtext.to_html(text, old.sheet, lambda attrs, style: None)  # every text's markup converts
    assert bool(old.fonts) == (game == "anno2070")  # only 2070 ships the font table
    assert game != "anno2070" or old.font_file(old.fonts["Default"])  # and its font files


STYLED = """<FRAME><PATHID>STYLED</PATHID><EXTENT><WIDTH>400</WIDTH><HEIGHT>300</HEIGHT></EXTENT>
<BUTTON><PATHID>GO</PATHID><BACKGROUND>data/config/gui/backgrounds/button.bkg</BACKGROUND><COLOR>HSV;0;100;50</COLOR>
<COLOROPERATION>3</COLOROPERATION><ICONGUID>100</ICONGUID><ICONSPACE><ICONLEFT>4</ICONLEFT><ICONTOP>2</ICONTOP>
<ICONWIDTH>0</ICONWIDTH><ICONHEIGHT>0</ICONHEIGHT></ICONSPACE><EXTENT><WIDTH>120</WIDTH><HEIGHT>30</HEIGHT></EXTENT>
<TEXTSETTING><TEXTGUID>6</TEXTGUID><FONT>Headlines</FONT>
<TEXTCOLOR>Blueish</TEXTCOLOR><MOUSEOVERTEXTCOLOR>16711680</MOUSEOVERTEXTCOLOR></TEXTSETTING></BUTTON>
<PANEL><PATHID>GROWS</PATHID><AUTOSIZE>1</AUTOSIZE><AUTOSIZEORIENTATION>1</AUTOSIZEORIENTATION>
<PADDINGBOTTOM>5</PADDINGBOTTOM><MAXSIZE>0; 60</MAXSIZE><EXTENT><TOP>100</TOP><WIDTH>50</WIDTH><HEIGHT>10</HEIGHT></EXTENT>
<PANEL><PATHID>IN</PATHID><EXTENT><TOP>20</TOP><WIDTH>10</WIDTH><HEIGHT>20</HEIGHT></EXTENT></PANEL></PANEL>
<LABEL><PATHID>STRETCHED</PATHID><ICONGUID>100</ICONGUID><ICONSTRETCH>1</ICONSTRETCH>
<EXTENT><TOP>200</TOP><WIDTH>40</WIDTH><HEIGHT>40</HEIGHT></EXTENT></LABEL></FRAME>"""
PROPERTIES = """<Properties><DefaultValues><Colors><KnownColors><Item><Color>-16776961</Color><ColorName>Blueish</ColorName>
</Item></KnownColors></Colors><Fonts><LocaFont><German><KnownFonts><Item><FontName>Headlines</FontName>
<FontFamilyName>MagistralTT Bold</FontFamilyName><FontFileName>fonts/MGS65__U.TTF</FontFileName><FontSize>22</FontSize>
<Weight>600</Weight></Item></KnownFonts></German></LocaFont></Fonts></DefaultValues></Properties>"""
ICONS = "<Icons><i><GUID>100</GUID><Icons><i><IconFileID>7</IconFileID><IconIndex>3</IconIndex></i></Icons></i></Icons>"


def _button_bkg() -> str:
    """Two states of one 8 x 8 tile: at rest, and the hover state tinted light blue (the menu bar's highlight)."""
    states = []
    for name, (h, s) in (("Normal", (0, 1)), ("Normal+MouseOver", (200, 0.2))):
        states.append(
            f"<i><LayoutName>{name}</LayoutName><flags>0</flags><m_XSize>1</m_XSize><m_YSize>1</m_YSize>"
            f"<m_Color_H>{h}</m_Color_H><m_Color_S>{s}</m_Color_S><m_Color_V>1</m_Color_V><m_Color_A>1</m_Color_A>"
            "<IconList><i><LocalFileID>0</LocalFileID><tileX>0</tileX><tileY>0</tileY><tileWidth>8</tileWidth>"
            "<tileHeight>8</tileHeight><flags>68</flags><Width>0</Width><Height>0</Height></i></IconList></i>"
        )
    return ("<BackgroundLayout><m_localIconFileMap><i>data/graphics/ui/button.png</i></m_localIconFileMap>"
            f"<BackgroundInfo>{''.join(states)}</BackgroundInfo></BackgroundLayout>")  # fmt: skip


@pytest.fixture
def styled(tree, tmp_path: Path):
    root = tmp_path / "game"
    for path, data in {GUI + "menudefinitions/styled.dlg": STYLED, GUI + "backgrounds/button.bkg": _button_bkg(),
                       "data/config/texteditor/properties.xml": PROPERTIES,
                       "data/config/game/icons.xml": ICONS}.items():  # fmt: skip
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(data, encoding="utf-8")
    texts = "5=Start\n6=<b>Go</b><br />on [PLAYER] <red>now</red>\n"
    (root / "data/loca/eng/txt/gui.txt").write_bytes(texts.encode("utf-16"))
    old = files.OldGui(gametree.loose_tree(root))
    return old, old.dialog(GUI + "menudefinitions/styled.dlg")


def test_colours_are_read_by_name_number_and_hsv():
    old = files.OldGui(gametree.loose_tree(Path("missing")))
    assert old.colour("16711680") == (255, 0, 0) and old.colour("0") is None and old.colour("") is None
    assert old.colour("HSV;120;100;100") == (0, 255, 0) and old.colour("NoSuchName") is None


def test_a_widget_is_drawn_in_the_state_picked_with_its_tints_and_text_colours(styled):
    old, dialog = styled
    rest = next(n for n in scene.build(old, dialog).walk() if n.name == "GO")
    assert rest.look.name == "Normal" and rest.look.tint is None  # (0, 1, 1, 1) leaves the picture as it is
    assert rest.tint == pytest.approx((0.5, 0.0, 0.0), abs=0.01)  # COLOR HSV;0;100;50: red at half value
    assert rest.text_color == (0, 0, 255)  # the known colour's ARGB
    hover = next(n for n in scene.build(old, dialog, "Normal+MouseOver").walk() if n.name == "GO")
    assert hover.look.name == "Normal+MouseOver" and hover.text_color == (255, 0, 0)
    assert hover.tint[0] == pytest.approx(0.5 * 0.8, abs=0.01) and hover.tint[2] == pytest.approx(0.0)
    other = next(n for n in scene.build(old, dialog, "Checked").walk() if n.name == "GO")
    assert other.look.name == "Normal"  # a state the background lacks: its state at rest


def test_icons_fonts_rich_text_and_autosize(styled):
    old, dialog = styled
    nodes = {n.name: n for n in scene.build(old, dialog).walk()}
    go, stretched, grows = nodes["GO"], nodes["STRETCHED"], nodes["GROWS"]
    assert (go.icon.atlas, go.icon.x, go.icon.y, go.icon.w) == ("data/graphics/ui/icons.tga", 3, -1, 16)
    assert go.icon_rect == (go.rect[0] + 4, go.rect[1] + 2, 16.0, 16.0)  # a space of no size: the icon's own
    assert stretched.icon_rect == stretched.rect
    assert go.font == files.Font("Headlines", "MagistralTT Bold", 22.0, 600, "fonts/MGS65__U.TTF")
    assert richtext.to_html(go.text, richtext.StyleSheet(), lambda _a, _s: None) == "<b>Go</b><br>on [PLAYER] now"
    assert grows.rect[3] == 20 + 20 + 5  # the child's bottom plus the padding
    assert richtext.to_html("a < b & c", richtext.StyleSheet(), lambda _a, _s: None) == "a &lt; b &amp; c"


def test_autosize_is_bounded_and_can_grow_upwards(styled):
    old, dialog = styled
    grows = next(w for w in dialog.walk() if w.path_id == "GROWS")
    grows.props["PADDINGBOTTOM"], grows.props["AUTOSIZEALIGN_VERTICAL"] = "50", "1"
    node = next(n for n in scene.build(old, dialog).walk() if n.name == "GROWS")
    assert node.rect[3] == 60.0 and node.rect[1] == 100.0 + 10.0 - 60.0  # MAXSIZE, the bottom kept


def _placed(tmp_path: Path, dialog: str, extra: dict | None = None, **options) -> dict[str, scene.Node]:
    for name, text in {"menudefinitions/one.dlg": dialog, **(extra or {})}.items():
        path = tmp_path / "game" / GUI / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    old = files.OldGui(gametree.loose_tree(tmp_path / "game"))
    return {n.name: n for n in scene.build(old, old.dialog(GUI + "menudefinitions/one.dlg"), **options).walk()}


SHEET = """/* the classes the tests use */ .uppercase { text-transform: uppercase; }
.gradientHeadline { color: #ffffff; gradient-color: #8bb6ff; } tthead { font-size: 26px; font-weight: bold; }
.tooltipicon { width: 32px; height: 32px; } .costs .tooltip-icon { width: 13px; }"""


def test_a_label_takes_its_style_class_and_a_text_its_styles_tables_and_icons(tmp_path):
    nodes = _placed(tmp_path, """<FRAME><PATHID>F</PATHID><EXTENT><WIDTH>400</WIDTH><HEIGHT>300</HEIGHT></EXTENT>
<LABEL><PATHID>HEAD</PATHID><TEXT>title</TEXT><TEXTSETTING><STYLESELECTOR>uppercase</STYLESELECTOR></TEXTSETTING>
<EXTENT><WIDTH>100</WIDTH><HEIGHT>20</HEIGHT></EXTENT></LABEL>
<LABEL><PATHID>GLOW</PATHID><TEXTSETTING><STYLESELECTOR>gradientHeadline</STYLESELECTOR></TEXTSETTING></LABEL></FRAME>""",
                    {"textstyles.css": SHEET})  # fmt: skip
    assert nodes["HEAD"].text == "TITLE" and nodes["HEAD"].style.upper
    assert nodes["GLOW"].style.color == ((255 + 0x8B) // 2, (255 + 0xB6) // 2, 255)  # the gradient's mean
    sheet = richtext.StyleSheet.parse(SHEET)
    assert "costs" not in sheet.styles and sheet.get("TTHEAD").size == 26.0  # a descendant rule is left out
    icons = []
    out = richtext.to_html('<tthead>Head</tthead><table><tr><td width="20" bgcolor="x">a</td></tr></table>'
                           '<icon guid="100" type="autosize" class="tooltipicon"/><font color="FF00FF">m</font><x>y</x>',
                           sheet, lambda attrs, style: icons.append((attrs, style)) or ("icon:100", 16, 16))  # fmt: skip
    assert out.startswith('<span style="font-size:26px;font-weight:700">Head</span><table><tr><td width="20">a</td>')
    assert '<img src="icon:100" width="16" height="16">' in out and '<span style="color:#ff00ff">m</span>' in out
    assert out.endswith("y") and icons[0][1].width == 32.0  # an element of no style keeps its text
    assert richtext.icon_guids('a <icon guid="7" /> b <ICON guid="8">') == ["7", "8"]


WIDE = """<FRAME><PATHID>F</PATHID><LAYOUTMODEH>1</LAYOUTMODEH><LAYOUTMODEV>1</LAYOUTMODEV>
<EXTENT><WIDTH>100</WIDTH><HEIGHT>50</HEIGHT></EXTENT>
<PANEL><PATHID>RIGHT</PATHID><LAYOUTMODEH>4</LAYOUTMODEH><EXTENT><LEFT>1200</LEFT><WIDTH>80</WIDTH></EXTENT></PANEL>
<PANEL><PATHID>MID</PATHID><LAYOUTMODEH>3</LAYOUTMODEH><EXTENT><LEFT>600</LEFT><WIDTH>80</WIDTH></EXTENT></PANEL>
<PANEL><PATHID>FILL</PATHID><LAYOUTMODEH>1</LAYOUTMODEH><EXTENT><LEFT>10</LEFT><WIDTH>1260</WIDTH></EXTENT></PANEL>
<PANEL><PATHID>STAY</PATHID><EXTENT><LEFT>10</LEFT><WIDTH>80</WIDTH></EXTENT></PANEL></FRAME>"""


def test_a_wider_screen_moves_and_stretches_by_the_modes(tmp_path):
    same = _placed(tmp_path, WIDE)
    assert same["F"].rect[2] == 1280.0 and same["RIGHT"].rect[0] == 1200.0  # the reference: as stored
    wide = _placed(tmp_path, WIDE, screen=scene.SCREENS["16:9"])
    grown = 1820.0 - 1280.0
    assert wide["F"].rect == (0.0, 0.0, 1820.0, 1024.0)
    assert wide["RIGHT"].rect[0] == 1200.0 + grown and wide["MID"].rect[0] == 600.0 + grown / 2
    assert wide["FILL"].rect[2] == 1260.0 + grown and wide["STAY"].rect[0] == 10.0
    corner = WIDE.replace("<LAYOUTMODEH>1</LAYOUTMODEH><LAYOUTMODEV>1</LAYOUTMODEV>",
                          "<ALIGNMENT_LEFTHORIZONTAL>2</ALIGNMENT_LEFTHORIZONTAL>"
                          "<ALIGNMENT_RIGHTHORIZONTAL>2</ALIGNMENT_RIGHTHORIZONTAL>", 1)  # fmt: skip
    pinned = _placed(tmp_path, corner.replace("<WIDTH>100</WIDTH>", "<LEFT>900</LEFT><WIDTH>380</WIDTH>", 1),
                     screen=scene.SCREENS["16:9"])  # fmt: skip
    assert pinned["F"].rect[:3] == (900.0 + grown, 0.0, 380.0)  # fixed to the right edge of the screen


def test_an_instance_is_drawn_with_its_component(tmp_path):
    custom = """<FRAME><PATHID>LIB</PATHID><CUSTOM><PATHID>WARE</PATHID><UNIQUEID>7</UNIQUEID>
<EXTENT><WIDTH>60</WIDTH><HEIGHT>40</HEIGHT></EXTENT><PANEL><PATHID>WARE_ICON</PATHID>
<EXTENT><LEFT>2</LEFT><TOP>3</TOP><WIDTH>20</WIDTH><HEIGHT>20</HEIGHT></EXTENT></PANEL></CUSTOM></FRAME>"""
    nodes = _placed(
        tmp_path,
        """<FRAME><PATHID>F</PATHID><EXTENT><WIDTH>400</WIDTH><HEIGHT>300</HEIGHT></EXTENT>
<INSTANCE><PATHID>W1</PATHID><STRUCTTEMPLATEREF>7</STRUCTTEMPLATEREF><EXTENT><LEFT>100</LEFT><TOP>50</TOP></EXTENT>
<PANEL><PATHID>OWN</PATHID></PANEL></INSTANCE></FRAME>""",
        {"menudefinitions/__custom_windows.dlg": custom},
    )
    assert nodes["W1"].rect == (100.0, 50.0, 60.0, 40.0)  # the component's size, the instance's place
    assert nodes["WARE_ICON"].rect[:2] == (102.0, 53.0) and [c.name for c in nodes["W1"].children] == [
        "WARE_ICON", "OWN"]  # fmt: skip


def test_a_label_resizes_its_parent_to_its_text(tmp_path):
    nodes = _placed(tmp_path, """<FRAME><PATHID>F</PATHID><EXTENT><WIDTH>400</WIDTH><HEIGHT>300</HEIGHT></EXTENT>
<PANEL><PATHID>BOX</PATHID><EXTENT><LEFT>10</LEFT><WIDTH>100</WIDTH><HEIGHT>30</HEIGHT></EXTENT>
<LABEL><PATHID>NAME</PATHID><TEXT>a longer name</TEXT><TEXTSETTING><RESIZEPARENTWINDOW>1</RESIZEPARENTWINDOW>
</TEXTSETTING><EXTENT><LEFT>5</LEFT><WIDTH>80</WIDTH><HEIGHT>20</HEIGHT></EXTENT></LABEL></PANEL></FRAME>""",
                    measure=lambda text, font, width, style: (150.0, 18.0))  # fmt: skip
    assert nodes["NAME"].rect[2:] == (150.0, 20.0)  # the width only: 1
    assert nodes["BOX"].rect[2] == 5.0 + 150.0 + 15.0  # the label's place, its text, the stored margin of 15
    w, h = scene.estimate("one two", None, 0.0)
    assert w > 0 and h == 14.0 * 1.25 and scene.estimate("one two three four", None, 30.0)[1] > h


def test_a_box_layout_stacks_a_child_at_the_size_its_autosize_gives(tmp_path):
    grows = ("<PANEL><PATHID>GROWS</PATHID><AUTOSIZE>1</AUTOSIZE><AUTOSIZEORIENTATION>1</AUTOSIZEORIENTATION>"
             "<EXTENT><WIDTH>50</WIDTH><HEIGHT>10</HEIGHT></EXTENT>"
             "<PANEL><PATHID>IN</PATHID><EXTENT><TOP>5</TOP><WIDTH>10</WIDTH><HEIGHT>35</HEIGHT></EXTENT></PANEL></PANEL>")  # fmt: skip
    nodes = _placed(
        tmp_path,
        f"""<FRAME><PATHID>F</PATHID><EXTENT><WIDTH>400</WIDTH><HEIGHT>300</HEIGHT></EXTENT>
<BOXLAYOUT><PATHID>BOX</PATHID><YSPACE>6</YSPACE><LINE_ORDER>0</LINE_ORDER><LINECOLUMNCOUNT>9</LINECOLUMNCOUNT>
<EXTENT><TOP>100</TOP><WIDTH>200</WIDTH><HEIGHT>200</HEIGHT></EXTENT>{grows}
<PANEL><PATHID>NEXT</PATHID><EXTENT><WIDTH>20</WIDTH><HEIGHT>8</HEIGHT></EXTENT></PANEL></BOXLAYOUT></FRAME>""",
    )
    assert nodes["GROWS"].rect[1:] == (100.0, 50.0, 40.0)  # its child's bottom
    assert nodes["NEXT"].rect[1] == 100.0 + 40.0 + 6.0  # below the grown size, not the stored 10
    assert nodes["IN"].rect[1] == 100.0 + 5.0  # moved with the panel the box placed


def test_a_widget_growing_upwards_takes_what_it_holds_along(styled):
    old, dialog = styled
    grows = next(w for w in dialog.walk() if w.path_id == "GROWS")
    grows.props["AUTOSIZEALIGN_VERTICAL"] = "1"
    nodes = {n.name: n for n in scene.build(old, dialog).walk()}
    top = nodes["GROWS"].rect[1]
    assert top == 100.0 + 10.0 - 45.0 and nodes["IN"].rect[1] == top + 20.0  # still 20 below the panel's top


def test_a_page_shows_the_state_asked_for_last_whichever_read_ends_first(styled):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.guiold.view import OldGuiPage

    QApplication.instance() or QApplication([])
    old, _ = styled
    page = OldGuiPage(old.tree)
    path = GUI + "menudefinitions/styled.dlg"
    page.sync_open = True
    page.open(path)
    page.state.setCurrentText("Normal+MouseOver")
    entry = page.files.entries[path]
    late = page._read(entry, "Normal")  # the read of the state before, ending after the newer one
    page._opened(entry, late, (path, "Normal"))
    assert next(n for n in page.root.walk() if n.name == "GO").look.name == "Normal+MouseOver"
    page.close()


def test_the_page_draws_a_state_icons_and_rich_text(styled, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.guiold.view import OldGuiPage

    qt = QApplication.instance() or QApplication([])
    old, _ = styled
    page = OldGuiPage(old.tree)
    page.sync_open = True
    page.open(GUI + "menudefinitions/styled.dlg")
    page.state.setCurrentText("Normal+MouseOver")  # opens the file again in that state
    go = next(n for n in page.root.walk() if n.name == "GO")
    assert go.look.name == "Normal+MouseOver"
    page.screen.setCurrentText("16:9")  # and on a wider screen
    assert page.stage.screen == scene.SCREENS["16:9"]
    w, h = page.stage.measure("a longer text", None, 0.0)
    assert w > 0 and h > 0 and page.stage.measure("a longer text", None, w / 3)[1] > h  # wrapped: more lines
    page.resize(1000, 700)
    page.show()
    qt.processEvents()
    assert not page.stage.grab().isNull()
    page.close()


def test_a_resizing_label_is_measured_in_its_class_and_stacked_at_its_measured_size(tmp_path):
    asked = []

    def measure(text, font, width, style):
        asked.append(style)
        return ((style.size if style is not None else 10.0) * len(text), 18.0)

    nodes = _placed(tmp_path, """<FRAME><PATHID>F</PATHID><EXTENT><WIDTH>400</WIDTH><HEIGHT>300</HEIGHT></EXTENT>
<BOXLAYOUT><PATHID>BOX</PATHID><XSPACE>4</XSPACE><LINECOLUMNCOUNT>9</LINECOLUMNCOUNT>
<EXTENT><WIDTH>400</WIDTH><HEIGHT>50</HEIGHT></EXTENT>
<LABEL><PATHID>NAME</PATHID><TEXT>abcd</TEXT><TEXTSETTING><RESIZEPARENTWINDOW>1</RESIZEPARENTWINDOW>
<STYLESELECTOR>big</STYLESELECTOR></TEXTSETTING><EXTENT><WIDTH>20</WIDTH><HEIGHT>20</HEIGHT></EXTENT></LABEL>
<PANEL><PATHID>NEXT</PATHID><EXTENT><WIDTH>10</WIDTH><HEIGHT>10</HEIGHT></EXTENT></PANEL></BOXLAYOUT></FRAME>""",
                    {"textstyles.css": ".big { font-size: 30px; }"}, measure=measure)  # fmt: skip
    assert asked[0].size == 30.0 and nodes["NAME"].rect[2] == 120.0  # in the class's size, as the page draws it
    assert nodes["NEXT"].rect[0] == nodes["NAME"].rect[0] + 120.0 + 4.0  # after the measured label, not its 20


def test_the_page_measures_a_text_in_its_class_as_it_draws_it(tree):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.guiold.view import OldGuiPage

    QApplication.instance() or QApplication([])
    page = OldGuiPage(tree)
    small = page.stage.measure("a longer text", None, 0.0, richtext.Style(size=10.0))
    large = page.stage.measure("a longer text", None, 0.0, richtext.Style(size=30.0))
    assert large[0] > 2 * small[0]
    page.close()


def test_the_sheets_box_model_becomes_margins_paddings_and_blocks():
    sheet = richtext.StyleSheet.parse(""".block { display: block; margin-top: 4px; padding-left: 12px;
text-align: center; line-height: 1.5em; font-size: 10px; } p { margin-bottom: 0.6em; } .pad { padding-left: 3px; }
.ruler { display: block; background-color: white; }""")  # fmt: skip
    out = richtext.to_html('<x class="block">a</x><p>t</p><table><tr><td class="pad">c</td></tr></table>'
                           '<y class="ruler"></y>', sheet, lambda _a, _s: None)  # fmt: skip
    assert out.startswith('<div style="margin-top:4px;margin-left:12px;text-align:center;line-height:15px;'
                          'font-size:10px">a</div><p style="margin-bottom:8.4px">t</p>')  # fmt: skip
    assert '<td style="padding-left:3px">c</td>' in out  # a cell keeps its padding
    assert out.endswith('<div style="background-color:#ffffff"></div>')


BOXES = r"""tthead { display: inline-block; width: 100%; padding-left: 8px;
background-image: url("data\\config\\gui\\Backgrounds\\head.bkg"); }
ttbody { display: block; margin-top: 6px; background-image: url("data\\config\\gui\\Backgrounds\\body.bkg"); }
.cost { display: inline-block; width: 40px; text-align: right; }"""


def test_an_inline_block_and_a_box_on_a_background_picture_are_drawn_as_boxes_of_their_own():
    sheet = richtext.StyleSheet.parse(BOXES)
    asked = []

    def box(style, inner, _ancestors):
        asked.append((style.inline, style.block, style.picture, style.share, inner))
        return f"box:{len(asked)}", 50.0, 20.0

    out = richtext.to_html('<tthead>Head <b>x</b></tthead><ttbody>A <tthead>in</tthead></ttbody>'
                           ' and <span class="cost">12</span>', sheet, lambda _a, _s: None, box)  # fmt: skip
    assert asked == [(True, False, "data/config/gui/backgrounds/head.bkg", 1.0, "Head <b>x</b>"),
                     (False, True, "data/config/gui/backgrounds/body.bkg", None, "A <tthead>in</tthead>"),
                     (True, False, None, None, "12")]  # fmt: skip
    assert out == ('<img src="box:1" width="50" height="20"><div style="margin-top:6px"><img src="box:2" '
                   'width="50" height="20"></div> and <img src="box:3" width="50" height="20">')  # fmt: skip
    assert richtext.box_pictures("<ttbody><tthead>a</tthead></ttbody>", sheet) == [
        "data/config/gui/backgrounds/body.bkg", "data/config/gui/backgrounds/head.bkg"]  # fmt: skip
    assert richtext.to_html("<tthead>Head</tthead>", sheet, lambda _a, _s: None) == '<span style="">Head</span>'


def test_the_page_draws_a_box_at_its_width_with_its_padding_inside():
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QFont, QTextDocument
    from PySide6.QtWidgets import QApplication

    from annotoolbox.guiold.view import BOX_SCALE, Atlases, Stage

    QApplication.instance() or QApplication([])
    stage = Stage(Atlases(None))
    style = richtext.StyleSheet.parse(BOXES).get("tthead")
    doc, font = QTextDocument(), QFont()
    font.setPixelSize(14)
    name, w, h = stage._box(doc, style, "Head", (font, (255, 255, 255), 300.0))
    assert w == 300.0 and h > 10  # a share of the width around it
    image = doc.resource(QTextDocument.ResourceType.ImageResource, name)
    assert image.width() == round(300 * BOX_SCALE) and image.height() == round(h * BOX_SCALE)
    cost = stage._box(doc, richtext.StyleSheet.parse(BOXES).get("cost"), "12", (font, (255, 255, 255), 300.0))
    assert cost[1] == 40.0 and cost[0] != name  # its own width, a resource of its own


def test_an_img_draws_the_loose_picture_it_names(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from annotoolbox.guiold.view import OldGuiPage

    QApplication.instance() or QApplication([])
    picture = tmp_path / "game" / "data/graphics/ui/datalog/01a.jpg"
    picture.parent.mkdir(parents=True)
    QImage(24, 12, QImage.Format.Format_RGB32).save(str(picture), "JPG")
    text = 'see <img src="data/graphics/ui/datalog/01a.jpg" /> and <img src=[REWARD_PICTURE] />'
    assert richtext.picture_paths(text) == ["data/graphics/ui/datalog/01a.jpg"]  # the game fills a [TAG]
    page = OldGuiPage(gametree.loose_tree(tmp_path / "game"))
    from PySide6.QtGui import QTextDocument

    shown = richtext.to_html(text, richtext.StyleSheet(), lambda attrs, style: page.stage._icon(QTextDocument(),
                             attrs, style, 14.0))  # fmt: skip
    assert shown == 'see <img src="img:data/graphics/ui/datalog/01a.jpg" width="24" height="12"> and '
    page.close()


def test_a_scroll_box_shows_what_it_holds_inside_it_only(tmp_path):
    nodes = _placed(
        tmp_path,
        """<FRAME><PATHID>F</PATHID><EXTENT><WIDTH>400</WIDTH><HEIGHT>300</HEIGHT></EXTENT>
<SCROLLBOX><PATHID>LIST</PATHID><EXTENT><WIDTH>100</WIDTH><HEIGHT>50</HEIGHT></EXTENT>
<PANEL><PATHID>LONG</PATHID><EXTENT><WIDTH>100</WIDTH><HEIGHT>200</HEIGHT></EXTENT></PANEL></SCROLLBOX></FRAME>""",
    )
    root = nodes["F"]
    assert root.at(50, 20) is nodes["LONG"] and root.at(50, 120) is root  # below the box: not what it holds


def test_a_click_on_a_scroll_boxs_border_picks_the_box_not_what_is_cut_away_there():
    """The drawing cuts what a scroll box holds to its client area; a click there must not pick it either."""
    kid = scene.Node(scene.Widget("PANEL", "", None, []), {}, (0.0, 0.0, 100.0, 200.0))
    look = files.Look("frame", 1, 1, (), offsets=(0, 10, 0, 10))
    box = scene.Node(scene.Widget("SCROLLBOX", "", None, []), {}, (0.0, 0.0, 100.0, 50.0), look=look, children=[kid])
    assert box.at(50, 5) is box and box.at(50, 45) is box and box.at(50, 25) is kid


def test_the_children_follow_a_size_their_parents_autosize_gives_by_their_modes(tmp_path):
    nodes = _placed(tmp_path, """<FRAME><PATHID>F</PATHID><EXTENT><WIDTH>400</WIDTH><HEIGHT>300</HEIGHT></EXTENT>
<PANEL><PATHID>CARD</PATHID><AUTOSIZE>1</AUTOSIZE><AUTOSIZEORIENTATION>1</AUTOSIZEORIENTATION>
<EXTENT><WIDTH>100</WIDTH><HEIGHT>60</HEIGHT></EXTENT>
<PANEL><PATHID>BACK</PATHID><LAYOUTMODEV>1</LAYOUTMODEV><EXTENT><WIDTH>100</WIDTH><HEIGHT>60</HEIGHT></EXTENT>
<PANEL><PATHID>EDGE</PATHID><LAYOUTMODEV>4</LAYOUTMODEV><EXTENT><TOP>50</TOP><WIDTH>5</WIDTH><HEIGHT>10</HEIGHT>
</EXTENT></PANEL></PANEL>
<PANEL><PATHID>TEXT</PATHID><EXTENT><WIDTH>80</WIDTH><HEIGHT>40</HEIGHT></EXTENT></PANEL>
<PANEL><PATHID>CLOSE</PATHID><LAYOUTMODEV>4</LAYOUTMODEV><EXTENT><TOP>45</TOP><WIDTH>10</WIDTH><HEIGHT>10</HEIGHT>
</EXTENT></PANEL>
<PANEL><PATHID>MID</PATHID><LAYOUTMODEV>3</LAYOUTMODEV><EXTENT><TOP>25</TOP><WIDTH>10</WIDTH><HEIGHT>10</HEIGHT>
</EXTENT></PANEL></PANEL></FRAME>""")  # fmt: skip
    assert nodes["CARD"].rect[3] == 40.0  # the text's extent: the background and the edge pieces do not make it
    assert nodes["BACK"].rect[3] == 40.0 and nodes["EDGE"].rect[1] == 50.0 - 20.0  # fill, and what it holds follows
    assert nodes["CLOSE"].rect[1] == 45.0 - 20.0 and nodes["MID"].rect[1] == 25.0 - 10.0


def test_a_rich_text_is_laid_out_once_and_again_for_a_new_placing(styled):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.guiold.view import OldGuiPage

    qt = QApplication.instance() or QApplication([])
    old, _ = styled
    page = OldGuiPage(old.tree)
    page.sync_open = True
    page.open(GUI + "menudefinitions/styled.dlg")
    page.resize(800, 600)
    page.show()
    page.stage.grab()
    laid = dict(page.stage._laid)
    assert laid  # GO's text is markup
    page.stage.grab()
    assert all(page.stage._laid[k] is doc for k, doc in laid.items())  # a paint draws what was laid out
    page.state.setCurrentText("Normal+MouseOver")  # placed again: laid out again with its first paint
    assert not page.stage._laid
    page.close()
    qt.processEvents()


RULES = r"""p { margin-bottom: 4px; }
.x { color: #00ff00; }
.a .b { color: #ff0000; }
.newsbody hr { display: block; height: 1px; background-image: url("data\config\gui\Backgrounds\line.bkg"); }
buffname icon { height: 22px; }
helperquestmailtext b { color: #e0bf22; font-weight: bolder; }
td.cell { width: 100px; }
a:hover { color: #aaaaff; }
.wide { width: 300px; margin-top: 2px; padding-left: 5px; padding-right: 3px;
background-image: url("data\config\gui\Backgrounds\table.bkg"); }
.bar { padding-left: 8px; background-image: url("data\config\gui\Backgrounds\bar.bkg"); }"""


def test_the_sheets_descendant_rules_style_an_element_by_its_ancestors():
    sheet = richtext.StyleSheet.parse(RULES)
    assert len(sheet.rules) == 9  # the state rule is left out
    assert sheet.match(("span", ("b",)), (("x", ("a",)),)).color == (255, 0, 0)  # .a .b: an ancestor with a
    assert sheet.match(("span", ("b",)), ()) is None and sheet.match(("td", ("cell",)), ()).width == 100.0
    merged = sheet.match(("p", ("x", "other")), ())  # p's rule and the class's both apply
    assert merged.color == (0, 255, 0) and merged.margin[2] == 4.0
    assert sheet.match(("hr", ()), (("", ("newsbody",)), ("b", ()))).picture == "data/config/gui/backgrounds/line.bkg"
    asked, icons = [], []

    def box(style, inner, path):
        asked.append((style.height, inner, path))
        return "box:1", 50.0, 1.0

    text = ('<span class="newsbody">a<hr/>b</span><buffname>z <icon guid="5"/></buffname>'
            '<helperquestmailtext>x <b>y</b></helperquestmailtext><b>n</b><z class="b">c</z>')  # fmt: skip
    out = richtext.to_html(text, sheet, lambda attrs, style: icons.append(style) or None, box, around=(("", ("a",)),))
    assert asked == [(1.0, "", (("", ("a",)), ("span", ("newsbody",)), ("hr", ())))]  # the line, as a box
    assert '<div style=""><img src="box:1" width="50" height="1"></div>' in out
    assert icons[0].height == 22.0 and '<b style="color:#e0bf22;font-weight:700">y</b>' in out
    assert "<b>n</b>" in out and '<span style="color:#ff0000">c</span>' in out  # .b inside the label's class a
    assert richtext.box_pictures(text, sheet) == ["data/config/gui/backgrounds/line.bkg"]


def test_tables_and_cells_with_background_pictures_are_listed_for_the_page_with_their_widths():
    sheet = richtext.StyleSheet.parse(RULES)
    frames = []
    text = ('<table class="wide"><tr><td class="bar">a</td><td class="cell">b</td></tr>'
            '<tr><td><table><tr><td class="bar">n</td></tr></table></td></tr></table>')  # fmt: skip
    out = richtext.to_html(text, sheet, lambda _a, _s: None, frames=frames)
    bar = "data/config/gui/backgrounds/bar.bkg"
    assert frames == [("data/config/gui/backgrounds/table.bkg", (0.0, 3.0, 0.0, 5.0), [bar, None, None]),
                      (None, (0.0, 0.0, 0.0, 0.0), [bar])]  # fmt: skip
    assert '<table style="margin-top:2px;margin-right:3px;margin-left:5px" width="308">' in out  # Qt's width: margins
    assert '<td width="100">b</td>' in out
    assert richtext.box_pictures(text, sheet) == ["data/config/gui/backgrounds/table.bkg", bar]


QT_TABLES = [
    '<table width="300" style="margin-left:9px" bgcolor="#ff0000"><tr><td bgcolor="#00ff00" style="padding-left:8px">'
    'a</td><td bgcolor="#0000ff">b<br>c</td></tr></table>',
    '<p>hi</p><table style="margin-top:14px;margin-left:3px" bgcolor="#ff0000"><tr><td bgcolor="#00ff00" '
    'style="padding:4px">a</td><td>b</td></tr><tr><td colspan="2" bgcolor="#0000ff" style="padding-bottom:6px">x'
    '<table bgcolor="#ffff00"><tr><td>n</td></tr></table></td></tr></table>',
    '<table bgcolor="#ff0000"><tr><td bgcolor="#00ff00"><table width="100" style="margin:5px"><tr><td>a</td></tr>'
    '</table></td><td valign="middle" bgcolor="#0000ff">b</td></tr><tr><td rowspan="2" bgcolor="#ffff00">r<br>s<br>t'
    "</td><td>1</td></tr><tr><td>2</td></tr></table><p>after</p>",
]


@pytest.mark.parametrize("markup", QT_TABLES)
def test_a_table_and_its_cells_are_found_where_qt_paints_their_own_colour(markup):
    pytest.importorskip("PySide6")
    import numpy as np
    from PySide6.QtGui import QColor, QImage, QPainter, QTextDocument
    from PySide6.QtWidgets import QApplication

    from annotoolbox.guiold.view import _tables, table_rects

    QApplication.instance() or QApplication([])
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    doc.setTextWidth(600)
    doc.setHtml(markup)
    image = QImage(600, 300, QImage.Format.Format_RGB32)
    image.fill(QColor(0, 0, 0))
    p = QPainter(image)
    doc.drawContents(p)
    p.end()
    px = np.frombuffer(image.constBits(), np.uint32).reshape(300, 600) & 0xFFFFFF

    def painted(colour) -> tuple:
        ys, xs = np.nonzero(px == colour & 0xFFFFFF)
        return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1

    seen = 0
    for table in _tables(doc.rootFrame()):
        box, cells = table_rects(doc, table)
        if table.format().background().style() != 0 and table.format().background().color().rgb() & 0xFFFFFF:
            assert painted(table.format().background().color().rgb()) == box.getCoords(), markup
            seen += 1
        for (r, c), rect in cells.items():
            brush = table.cellAt(r, c).format().background()
            if brush.style() != 0 and brush.color().rgb() & 0xFFFFFF:
                assert painted(brush.color().rgb()) == rect.getCoords(), (markup, r, c)
                seen += 1
    assert seen >= 3


def test_the_page_draws_the_pictures_of_tables_and_cells_behind_the_text():
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QApplication

    from annotoolbox.guiold.view import table_backdrops, table_rects

    QApplication.instance() or QApplication([])
    sheet, frames = richtext.StyleSheet.parse(RULES), []
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    doc.setTextWidth(600)
    doc.setHtml(richtext.to_html('<table class="wide"><tr><td class="bar">a</td><td>b</td></tr></table>', sheet,
                                 lambda _a, _s: None, frames=frames))  # fmt: skip
    got = table_backdrops(doc, frames)
    table = next(iter(doc.rootFrame().childFrames()))
    box, cells = table_rects(doc, table)
    assert [p for _r, p in got] == ["data/config/gui/backgrounds/table.bkg", "data/config/gui/backgrounds/bar.bkg"]
    assert got[0][0] == box.adjusted(-5, 0, 3, 0) and abs(box.width() - 300) < 1e-6  # the padding inside the picture
    assert got[1][0] == cells[(0, 0)]
    assert table_backdrops(doc, frames + frames) == []  # another number of tables than the markup's: none drawn


def test_what_a_box_holds_takes_its_colour_and_its_width_and_a_block_box_is_as_wide_as_the_text():
    """Audit of WP5e: a box inside a box was laid out in the colour and against the width of the text around the
    outer one; a block box without a width shrank to what it holds."""
    from types import SimpleNamespace

    pytest.importorskip("PySide6")
    from PySide6.QtGui import QFont, QTextDocument
    from PySide6.QtWidgets import QApplication

    from annotoolbox.guiold.view import Atlases, Stage

    QApplication.instance() or QApplication([])
    sheet = richtext.StyleSheet.parse(r""".outer { display: inline-block; width: 200px; padding-left: 10px;
    color: #ff0000; } .half { display: inline-block; width: 50%; } .block { display: block; margin-left: 20px;
    margin-right: 10px; background-image: url("data\config\x.bkg"); }""")
    stage = Stage(Atlases(None))
    stage.files = SimpleNamespace(sheet=sheet, icon=lambda guid: None, background=lambda path: {})
    calls, real = [], stage._box

    def spy(doc, style, inner, around, path=()):
        calls.append((around[1], around[2], path))
        return real(doc, style, inner, around, path)

    stage._box = spy
    font = QFont()
    font.setPixelSize(14)
    name, width, _h = stage._box(QTextDocument(), sheet.get("outer"), '<n class="half">in</n>', (font, (1, 2, 3), 600))
    assert width == 200 and calls[1][:2] == ((255, 0, 0), 190.0)  # the outer box's colour, its width less padding
    assert calls[1][2] == (("n", ("half",)),)
    _name, block, _h = stage._box(QTextDocument(), sheet.get("block"), "a", (font, (1, 2, 3), 600))
    assert block == 570  # the text's width less the block's margins
