"""Stand-ins for the UI conversion: object menus, the 2070 UI's panels, trees and built mods."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from annotoolbox.formats import phoenix, phoenixedit, phoenixread, phoenixwrite
from annotoolbox.sources.overlay import CSourceTree


SCHEMA = phoenix.load_schema()


UI_VERSIONS = {h: str(uuid.uuid5(uuid.NAMESPACE_URL, h)) for h in phoenix.full_schema(SCHEMA)}


CLASS = phoenix.ref_bytes("8f8600a5", "11111111-2222-3333-4444-555555555555")  # a scene data class, as ObjectTemplate


F1, F2, F3, F4 = 0x2D8F0634, 0x28B5E4D0, 0x2B2357A9, 0x2988DDF5  # the field path of a name, as the shipped scenes


LOC = 0x2BAAF451


GUIDS = {k: str(uuid.uuid5(uuid.NAMESPACE_URL, f"om-test/{k}")) for k in ("host", "mid", "leaf", "panel")}


def game_object(wf: phoenixedit.CWidgetFile, name: str) -> int:
    uid = wf.add_property(name, "gameobject")
    phoenixedit.set_raw(wf.m_properties[-1], "ObjectTemplate", CLASS)
    return uid


def instance(kind: str, name: str, template: str | None = None) -> dict:
    node = phoenixedit.new_object(SCHEMA, kind, guid=uuid.uuid4().bytes_le, versions=UI_VERSIONS)
    phoenixedit.set_name(node, name)
    if template:
        phoenixedit.set_ref(node, "Template", phoenixedit.WIDGET_TEMPLATE, template)
    return node


def widget(guid: str) -> tuple[dict, phoenixedit.CWidgetFile]:
    root = phoenixedit.new_object(
        SCHEMA, phoenixedit.WIDGET_TEMPLATE, guid=uuid.UUID(guid).bytes_le, root=True, versions=UI_VERSIONS
    )
    return root, phoenixedit.CWidgetFile(SCHEMA, root, versions=UI_VERSIONS)


GUI = "data/config/gui/"


BKG_DIR = GUI + "backgrounds/prototype/"


def ext(left=0, top=0, w=0, h=0) -> str:
    return f"<EXTENT><LEFT>{left}</LEFT><TOP>{top}</TOP><WIDTH>{w}</WIDTH><HEIGHT>{h}</HEIGHT></EXTENT>"


def ui_node(kind: str, name: str, inner: str = "", **props) -> str:
    extra = "".join(f"<{k}>{v}</{k}>" for k, v in props.items())
    return f"<{kind}><PATHID>{name}</PATHID>{extra}{inner}</{kind}>"


def label(name, x, y, w=60, h=19, color="TooltipBlue"):
    return ui_node("LABEL", name, ext(x, y, w, h) + f"<TEXTSETTING><TEXTCOLOR>{color}</TEXTCOLOR></TEXTSETTING>")


def icon(name, guid, x, y, w=20, h=20, kind="LABEL"):
    return ui_node(kind, name, ext(x, y, w, h), ICONGUID=guid)


def wares(panel, value, guid):
    inner = ui_node(
        "PANEL", value, ext(0, 0, 55, 22) + label("VALUE", 17, 3, 36) + icon("WARE_ICON", guid, 0, 2, kind="BUTTON")
    )
    return ui_node("PANEL", panel, ext(0, 0, 52, 110) + inner)


INFOBAR = ui_node(
    "FRAME",
    "INFOBAR",
    ui_node(
        "LABEL",
        "MAIN_PANEL",
        ext(2, 2, 491, 198)
        + ui_node(
            "BOXLAYOUT",
            "INFOBAR_LAYOUT",
            ext(0, 0, 456, 227)
            + ui_node(
                "PANEL",
                "RESOURCES_PANEL",
                ext(0, 0, 177, 67)
                + icon("SHOW_INFO_BUTTON", 2600013, 7, 6, 21, 21)
                + label("CURRENT_MONEY_VIEW", 31, 7)
                + icon("BALANCE_ICON", 2600015, 8, 34, 21, 21)
                + label("CURRENT_PROFIT_VIEW", 30, 35, 56, color="0")
                + icon("ECO_MANPOWER_ICON", 2600170, 90, 6)
                + label("CURRENT_ECO_MANPOWER", 113, 7, color="White")
                + icon("RESIDENTS_ICON", 2600014, 90, 35)
                + label("CURRENT_RESIDENTS_VIEW", 113, 35, 62),
                BACKGROUND=BKG_DIR + "infobar/infobar_bg_left.bkg",
            )
            + ui_node(
                "PANEL",
                "ISLAND_INFO",
                ext(175, 0, 876, 185)
                + ui_node(
                    "PANEL",
                    "CENTER_PANEL",
                    ext(0, 0, 175, 63)
                    + ui_node(
                        "LABEL",
                        "FERTILITY_BUTTON_OFFSET",
                        ext(6, 30, 104, 26)
                        + ui_node(
                            "BOXLAYOUT",
                            "FERTILITIES_LIST",
                            ext(7, 2, 94, 26) + "".join(icon(f"FERTILITY_{i}", -1, 0, 0, 22, 22) for i in range(1, 5)),
                            XSPACE=2,
                        ),
                    ),
                    BACKGROUND=BKG_DIR + "infobar/infobar_bg_center.bkg",
                )
                + ui_node(
                    "PANEL",
                    "RESOURCE_RESIZE_PANEL",
                    ext(146, 29, 0, 33)
                    + ui_node(
                        "PANEL",
                        "RESOURCE_PANEL0",
                        ext(0, 1, 45, 26)
                        + ui_node(
                            "BUTTON",
                            "RESOURCE0",
                            ext(0, 0, 51, 26)
                            + label("RESOURCE_AMOUNT", 22, 2, 13, 20)
                            + icon("RESOURCE_ICON", -1, -1, 0, 22, 22),
                            BACKGROUND=BKG_DIR + "infobar/infobar_resource_button.bkg",
                        ),
                    ),
                    BACKGROUND=BKG_DIR + "infobar/infobar_resource_background.bkg",
                )
                + ui_node(
                    "PANEL",
                    "WARES_PANEL_BG",
                    ext(177, 0, 56, 127)
                    + ui_node(
                        "BOXLAYOUT",
                        "WARES_BOX",
                        ext(26, 4, 284, 127)
                        + wares("ENERGY_PANEL", "ENERGY_VALUE", 2600065)
                        + wares("ECOBALANCE_PANEL", "ECOBALANCE_VALUE", 2600078),
                        XSPACE=0,
                    ),
                    BACKGROUND=BKG_DIR + "infobar/infobar_bg_right.bkg",
                ),
            ),
            XSPACE=0,
        ),
    ),
)


def slot_panel(name, x, guid):
    return ui_node(
        "PANEL",
        name,
        ext(x, 0, 78, 24) + icon(f"{name}_ICON", guid, 1, 0, 24, 24) + label(f"{name}_TEXT", 28, 1, 58, 18),
    )


BUTTONS = ui_node(
    "FRAME",
    "OBJECTMENU_BUTTONS",
    ext(918, 940, 362, 24)  # the info strip's own place, 30 above the menu bar's top line
    + ui_node(
        "CARDLAYOUT",
        "INFO_CARDS",
        ext(0, 0, 362, 24)
        + ui_node(
            "PANEL",
            "DEFAULT",
            ext(0, 0, 362, 24)
            + slot_panel("MAINTENANCE", 6, 2600002)
            + slot_panel("ENERGY", 85, 2600065)
            + slot_panel("ECOCOST", 162, 2600078),
        )
        + ui_node(
            "PANEL",
            "RESIDENCE",
            ext(0, 0, 362, 24)
            + slot_panel("BUILDING_INHABITANTS", 6, 2600113)
            + slot_panel("LEVEL_INHABITANTS", 104, 2600114),
        ),
    ),
)


SPECIAL = ui_node(  # the dialog behind the generic object menu (objectmenus2070): frame, head, name, category, icon
    "FRAME",
    "OBJECTMENU_SPECIAL",
    ext(0, 0, 362, 150)
    + ui_node(
        "PANEL",
        "BACKGROUND_BODY",
        ext(0, 0, 100, 50),
        BACKGROUND=BKG_DIR + "general_frames/general_menu_body_bg.bkg",
    )
    + ui_node("PANEL", "HEAD_LEFT", ext(0, 0, 54, 50), BACKGROUND=BKG_DIR + "general_frames/general_menu_head_left.bkg")
    + ui_node(
        "PANEL",
        "HEAD_RIGHT",
        ext(54, 0, 101, 50),
        BACKGROUND=BKG_DIR + "general_frames/general_menu_head_right.bkg",
    )
    + label("NAME", 46, 13, 217, 32, color="White")
    + label("CATEGORY", 46, 7, 160, 12)
    + ui_node("BUTTON", "OBJECT_BUTTON", ext(7, 6, 34, 34)),
    ALIGNMENT_LEFTHORIZONTAL=2,  # fixed to the right edge, as the shipped object menus are
)


def need_panel(name: str, guid: int, x: int) -> str:
    """One need panel of the residence dialog: its button on common_button, the anchor, the icon, bar and name."""
    box = ui_node(
        "BOXLAYOUT",
        "BOXLAYOUT",
        ext(0, 6, 163, 36)
        + ui_node("PANEL", "ANCHOR", ext(0, 0, 8, 40), BACKGROUND=BKG_DIR + "common_frames/common_buttonanchor.bkg")
        + ui_node(
            "PANEL",
            "BUTTON_CONTENT",
            ext(16, 0, 147, 40)
            + icon("DEMAND_ICON", guid, 11, 3, 28, 28)
            + ui_node(
                "PROGRESSBAR",
                "FULFILMENT_QUOTA",
                ext(44, 0, 113, 36),
                BACKGROUND=BKG_DIR + "objectmenu_frames/residencemenu/residence_demandpb_green.bkg",
            )
            + label("DEMAND_NAME", 51, 1, 117, 34, color="White"),
        ),
    )
    button = ui_node(
        "BUTTON", "BUTTON", ext(0, 0, 169, 48) + box, BACKGROUND=BKG_DIR + "common_buttons/common_button.bkg"
    )
    return ui_node("LABEL", name, ext(x, 20, 176, 49) + button)


RESIDENCE_DIALOG = ui_node(  # the residence dialog (residencemenu2070): the tab box and a need panel
    "FRAME",
    "OBJECTMENU_HOUSE",
    ext(0, 0, 362, 784)
    + ui_node(
        "LABEL",
        "DEMANDS_BACKGROUND",
        ext(3, 0, 357, 109),
        BACKGROUND=BKG_DIR + "general_frames/general_menu_tabbox.bkg",
    )
    + need_panel("FOOD_DEMAND_PANEL", 2501000, 379),
)


def bkg(width: int) -> str:
    tiles = "".join(
        f"<i><LocalFileID>0</LocalFileID><tileX>{x}</tileX><tileY>0</tileY><tileWidth>{w}</tileWidth>"
        f"<tileHeight>8</tileHeight><flags>{f}</flags><Width>0</Width><Height>0</Height></i>"
        for x, w, f in ((0, 2, 0), (2, 4, 4), (6, width, 0))
    )
    return (
        "<BackgroundLayout><m_localIconFileMap><i>data/graphics/ui/textures/gui.png</i></m_localIconFileMap>"
        "<BackgroundInfo><i><LayoutName>EmptyLayout</LayoutName><m_XSize>3</m_XSize><m_YSize>1</m_YSize>"
        f"<IconList>{tiles}</IconList></i></BackgroundInfo></BackgroundLayout>"
    )


ICON_GUIDS = (2600013, 2600015, 2600170, 2600014, 2600065, 2600078, 2600215, 2600002)


MENU_ICONS = (2600030, 2070001000, 2070001001, 2070001002, 2070001003)  # a tab's picture and four buildings'


ICON_GUIDS += MENU_ICONS


ICONS = (
    "<Icons>"
    + "".join(
        f"<i><GUID>{g}</GUID><Icons><i><IconFileID>30</IconFileID><IconIndex>{i}</IconIndex></i></Icons></i>"
        for i, g in enumerate(ICON_GUIDS)
    )
    + "</Icons>"
)


ICON_FILE_MAP = """<IconFileMap><IconFile><IsGameIcon>1</IsGameIcon><IconFilename>data/graphics/ui/icons/atlas.png</IconFilename>
<IconWidth>4</IconWidth><IconHeight>4</IconHeight><IconFileID>30</IconFileID></IconFile></IconFileMap>"""


BUILD_MENU_BKGS = (  # the backgrounds of the build menu panels (uipanels2070)
    "general_frames/general_tabmenu_head_left.bkg",
    "general_frames/general_tabmenu_head_right.bkg",
    "general_frames/general_tabmenu_row_bg.bkg",
    "general_frames/general_tabmenu_body_bg.bkg",
    "general_frames/general_menu_tabbox.bkg",
    "general_frames/general_menu_tab.bkg",
    "general_frames/general_menubuttons_bg.bkg",
    "common_buttons/common_bottombutton.bkg",
    "general_buttons/62x62_rect.bkg",
    "general_frames/general_build_menu_arrow_large.bkg",
    "general_frames/general_build_menu_arrow_medium.bkg",
    "general_frames/general_build_menu_arrow_small.bkg",
    "general_frames/general_menu_pattern.bkg",
    "general_buttons/62x62_rect_chain.bkg",
    "common_frames/common_light_edge_overlay.bkg",
    "common_frames/common_textbar_left_cut.bkg",
    "common_frames/common_textbar_right.bkg",
)


COLORS = """<Properties><Colors><Item><Color>-3482645</Color><ColorName>TooltipBlue</ColorName></Item>
<Item><Color>-51656</Color><ColorName>NegativeValueRed</ColorName></Item>
<Item><Color>-327681</Color><ColorName>White</ColorName></Item></Colors></Properties>"""


@pytest.fixture
def ui_tree2070(tmp_path: Path) -> CSourceTree:
    root = tmp_path / "src" / "Data1"
    files = {
        GUI + "menudefinitions/infobar.dlg": INFOBAR,
        GUI + "menudefinitions/objectmenu_buttons.dlg": BUTTONS,
        GUI + "menudefinitions/objectmenu_special.dlg": SPECIAL,
        GUI + "menudefinitions/objectmenu_house.dlg": RESIDENCE_DIALOG,
        GUI + "menudefinitions/objectmenu_marketplace.dlg": SPECIAL,  # the city centre's frame, as a plain one
        GUI + "menudefinitions/objectmenu_public.dlg": SPECIAL,
        GUI + "menudefinitions/objectmenu_resource.dlg": SPECIAL,  # a free slot's menu, as a plain one
        **{
            BKG_DIR + p: bkg(2)
            for p in (
                "common_buttons/common_button.bkg",
                "common_frames/common_buttonanchor.bkg",
                "objectmenu_frames/residencemenu/residence_demandpb_green.bkg",
                "objectmenu_frames/residencemenu/residence_demandpb_yellow.bkg",
                "objectmenu_frames/residencemenu/residence_demandpb_red.bkg",
            )
        },
        BKG_DIR + "general_frames/general_menu_head_left.bkg": bkg(2),
        BKG_DIR + "general_frames/general_menu_head_right.bkg": bkg(2),
        BKG_DIR + "infobar/infobar_bg_left.bkg": bkg(2),
        BKG_DIR + "infobar/infobar_bg_right.bkg": bkg(3),
        BKG_DIR + "infobar/infobar_bg_center.bkg": bkg(2),
        BKG_DIR + "infobar/infobar_resource_background.bkg": bkg(2),
        BKG_DIR + "infobar/infobar_resource_button.bkg": bkg(2),
        **{
            BKG_DIR + f"infobar/infobar_{name}.bkg": bkg(2)
            for name in ("costs_display", "bg_left_empty", "bg_right_empty", "resource_indicator_bad")
        },
        BKG_DIR + "general_frames/general_menu_body_bg.bkg": bkg(2),
        **{BKG_DIR + p: bkg(2) for p in BUILD_MENU_BKGS},
        "data/config/game/icons.xml": ICONS,
        GUI + "iconfilemap.xml": ICON_FILE_MAP,
        "data/config/texteditor/properties.xml": COLORS,
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / "data/graphics/ui/icons").mkdir(parents=True)
    (root / "data/graphics/ui/textures").mkdir(parents=True)
    PIL = pytest.importorskip("PIL.Image")
    PIL.new("RGBA", (64, 4), (0, 128, 255, 255)).save(root / "data/graphics/ui/icons/atlas.png")
    PIL.new("RGBA", (16, 8), (20, 40, 60, 255)).save(root / "data/graphics/ui/textures/gui.png")
    return CSourceTree(tmp_path / "src")


def copy_texture(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


TAB, TAB2, FOLDER = 2070003341, 2070003342, 2070003343


CHAIN, CHAIN2 = 2070004001, 2070004002


MENU = {
    "heads": {
        str(TAB): {"name": "Infrastructure", "icon": 2600030, "faction": "", "groups": [], "tip": 2070009001},
        str(TAB2): {"name": "Eco Workers", "icon": 0, "faction": "Ecos", "groups": []},
    },
    "chains": {
        str(CHAIN): {"building": 2070001000, "tiers": [[2070001001, 2070001002], [2070001003]]},
        str(CHAIN2): {"building": 2070001001, "tiers": []},
    },
    "bar": {
        "tabs": {str(TAB): 0, str(TAB2): 1},
        "parent": [[5001, TAB], [5002, TAB], [5003, TAB], [CHAIN, TAB2], [5003, TAB2], [5004, FOLDER], [FOLDER, TAB2]],
        "build": {str(CHAIN): 2070001000},
        "layout": {
            str(TAB): {
                "rows": [2, 1],
                "names": ["Infrastructure", "Harbour"],
                "kinds": ["Infrastructure", "Harbour"],
                "entries": [5001, 5002, 5003],
            },
            str(TAB2): {
                "rows": [3],
                "names": ["Production"],
                "kinds": ["Production"],
                "entries": [CHAIN, 5003, FOLDER],
            },
            str(FOLDER): {"rows": [1], "names": [""], "kinds": [""], "entries": [5004]},
        },
        "icons": {"5001": 2070001000, "5002": 2070001001, str(CHAIN): 2070001000, "5003": 2070001002, "5004": 999},
        "tips": {"5001": 1205035},
        "costs": {"5001": [[1010017, 500], [2070001015, 4]]},
        "upkeep": {"5001": 20},
    },
}


GUID_MAP = {"2070001000": 2070001100, "2070001001": 2070001101, "2070001002": 2070001102, "-5": 7}


