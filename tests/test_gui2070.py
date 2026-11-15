"""2070's UI files (gui2070): widgets, templates, backgrounds, support files."""

from annotoolbox.guiold import gui2070

DLG = """<FRAME><PATHID>home</PATHID><UNIQUEID>7</UNIQUEID><TEMPLATE_REF_ID>FRAME_TPL</TEMPLATE_REF_ID>
<EXTENT><LEFT>0</LEFT><TOP>485</TOP><WIDTH>477</WIDTH><HEIGHT>538</HEIGHT></EXTENT>
<BUTTON><PATHID>quit</PATHID><UNIQUEID>12</UNIQUEID><TEMPLATE_REF_ID>BTN</TEMPLATE_REF_ID>
<TEXTGUID>1234</TEXTGUID><EVENTLIST><EVENT><TYPE>Click</TYPE><SLOT>Todesktop_Click</SLOT></EVENT>
<EVENT><TYPE>MouseEnter</TYPE><SLOT>x</SLOT><SCRIPT>MainMenuUtils.CheckBoxMouseEnter()</SCRIPT></EVENT></EVENTLIST>
</BUTTON></FRAME>"""
TPL_BTN = "<TEMPLATE><PATHID>BTN</PATHID><TEMPLATE_REF_ID>BASE</TEMPLATE_REF_ID><EXTENT><WIDTH>352</WIDTH><HEIGHT>56</HEIGHT></EXTENT></TEMPLATE>"
TPL_BASE = "<TEMPLATE><PATHID>BASE</PATHID><EXTENT><WIDTH>10</WIDTH><HEIGHT>10</HEIGHT><LEFT>3</LEFT></EXTENT><FONT>StartScreen</FONT></TEMPLATE>"
BKG = """<BackgroundLayout><m_localIconFileMap><i>data/graphics/ui/a.png</i></m_localIconFileMap><BackgroundInfo><i>
<LayoutName>Normal</LayoutName><m_XSize>3</m_XSize><m_YSize>1</m_YSize>
<m_Color_H>0</m_Color_H><m_Color_S>1</m_Color_S><m_Color_V>1</m_Color_V><m_Color_A>1</m_Color_A>
<IconList><i><LocalFileID>0</LocalFileID><tileX>0</tileX><tileY>0</tileY><tileWidth>8</tileWidth><tileHeight>32</tileHeight>
<flags>0</flags><Width>8</Width><Height>32</Height></i><i><LocalFileID>0</LocalFileID><tileX>8</tileX><tileY>0</tileY>
<tileWidth>4</tileWidth><tileHeight>32</tileHeight><flags>4</flags><Width>4</Width><Height>32</Height></i></IconList>
</i></BackgroundInfo></BackgroundLayout>"""


def test_dialog_widgets_events_and_templates():
    home = gui2070.parse_widget_file(DLG)
    quit_ = home.children[0]
    assert home.cls == "CGUIWndFrame" and quit_.kind == "BUTTON" and quit_.uid == 12
    assert gui2070.extent(home.props) == (0, 485, 477, 538)
    assert [(e.slot, e.script) for e in quit_.events] == [
        ("Todesktop_Click", None),  # the dialog's own code
        ("x", "MainMenuUtils.CheckBoxMouseEnter()"),  # Lua
    ]
    tpls = gui2070.Templates([gui2070.parse_widget_file(TPL_BTN), gui2070.parse_widget_file(TPL_BASE)])
    eff = tpls.effective(quit_)
    assert eff["EXTENT"] == {"WIDTH": "352", "HEIGHT": "56", "LEFT": "3"} and eff["FONT"] == "StartScreen"
    assert tpls.missing(home.templates) == ["FRAME_TPL"]


def test_backgrounds_are_nine_slices():
    bkg = gui2070.parse_background(BKG)
    normal = bkg.states["Normal"]
    assert bkg.files == ["data/graphics/ui/a.png"] and normal.grid == (3, 1)
    assert [t.flags & gui2070.TILE_STRETCH_X for t in normal.tiles] == [0, 4]  # the middle stretches


def test_support_files():
    assert gui2070.tooltip_text_id("TOOLTIP_DISABLED", 1234) == 20_001_234
    assert gui2070.parse_color("HSV;210;20;92") == ("HSV", (210.0, 20.0, 92.0)) and gui2070.parse_color("255") == 255
    assert gui2070.parse_css("/* c */ p, h1 { color: red; font-size: 12 }") == [
        (["p", "h1"], {"color": "red", "font-size": "12"})
    ]
    assert gui2070.parse_layout_names("Button-Layouts:\n*Normal\nNormal+MouseOver\n") == {
        "Button": [("Normal", True), ("Normal+MouseOver", False)]
    }
    assert gui2070.read_gui_file("x/y.bkg.png", b"\xee\xee\xff\xff") is None  # a 4-byte preview stub
