"""The game's fonts read from the install: the registry and its fallback, 2205's font references and descriptors, the Texts
app's face, the old interface's font tables. Every font is one the test makes (tests/fakefont.py), none is a game's."""

from __future__ import annotations

import os
import struct
from fnmatch import fnmatch

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from annotoolbox.flashui import fonts2205, uirender
from annotoolbox.formats import feuread, ffdread
from annotoolbox.guiold import fontsloca
from annotoolbox.suite import gamefonts
from annotoolbox.textedit import fontchain, fontcover, textfont
from fakefont import make_ttf


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


class Tree:
    """Files by path, read the way the installs are (``read`` raises ``KeyError`` for a file that is not there)."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def read(self, path: str) -> bytes:
        return self.files[path]

    def exists(self, path: str) -> bool:
        return path in self.files

    def glob(self, pattern: str) -> list[str]:
        return [p for p in self.files if fnmatch(p, pattern)]


def test_the_registry_loads_a_font_once_per_content_and_says_why_one_is_missing():
    tree = Tree(
        {"data/a.ttf": make_ttf("Registry Face"), "data/copy.ttf": make_ttf("Registry Face"), "data/bad.ttf": b"junk"}
    )
    registry = gamefonts.FontRegistry(tree.read)
    first = registry.face("DATA\\A.ttf")  # any case, either slash
    assert first.loaded and first.family == "Registry Face"
    assert registry.face("data/copy.ttf").family == first.family  # the same bytes are not registered twice
    assert not registry.face("data/missing.ttf").loaded and not registry.face("data/bad.ttf").loaded
    notes = registry.notes()
    assert len(notes) == 2 and "data/missing.ttf" in notes[0] and "data/bad.ttf" in notes[1]
    assert registry.loaded == 2


def _ffd(family: str, codes: list[int], picture: str = "Test_Default_1.png") -> bytes:
    """A font descriptor in the layout of the shipped ones (docs/formats/anno2205-ui-format.md)."""
    n = len(codes)
    name = family.encode()
    out = struct.pack("<4H", 0x8009, 293, n, 32) + bytes(36) + b"\xfd\x0f\x84\x01" + bytes([len(name)]) + name
    out += struct.pack("<H", n) + b"".join(struct.pack("<H", 2 * n + 2 + 2 * k) for k in range(n + 1))
    out += struct.pack("<H", 0x11) * n + b"".join(struct.pack("<H", c) for c in codes)
    out += bytes(2 * n * 2) + picture.encode() + b"\x00"
    for k, code in enumerate(codes):
        out += struct.pack("<HB4f4h", code, 0, 0.25 * k, 0.5, 0.25 * k + 0.125, 0.75, -1, 2, 30, 40)
    return out + struct.pack("<H", 1)


def test_a_font_descriptor_gives_its_family_picture_and_glyph_rectangles():
    desc = ffdread.read(_ffd("Roboto Light", [0x20, 0x41, 0x42]))
    assert (desc.family, desc.picture, len(desc.glyphs)) == ("Roboto Light", "Test_Default_1.png", 3)
    assert desc.has("A") and not desc.has("z")
    assert desc.glyphs[1].uv == (0.25, 0.5, 0.375, 0.75) and desc.glyphs[1].box == (-1, 2, 30, 40)
    for broken in (b"short", b"\x00" * 64, _ffd("X", [1, 2])[:-40]):
        with pytest.raises(ValueError):
            ffdread.read(broken)


class Ui:
    """What ``Fonts2205`` asks of ``UiFiles``: the file bytes, and None for a file that is not there."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def read(self, path: str) -> bytes | None:
        return self.files.get(path)

    def file(self, path: str) -> bytes:
        return self.files[path]


def _roboto_install(ttf: bool = True) -> Ui:
    files = {
        "data/ui/roboto_light_default.ffd": _ffd("Roboto Light", [0x41]),
        "data/ui/roboto_default.ffd": _ffd("Roboto", [0x41]),
        "data/ui/roboto_medium_default.ffd": _ffd("Roboto Medium", [0x41]),
        "data/ui/arial_default.ffd": _ffd("Arial", [0x41]),
    }
    if ttf:
        files["data/ui/chromium/roboto-light.ttf"] = make_ttf("Roboto Test Face")
        files["data/ui/chromium/roboto-regular.ttf"] = make_ttf("Roboto Test Face")
    return Ui(files)


def _ref(file: str, name: str) -> feuread.FontRef:
    return feuread.FontRef(1, file, name, 0, ())


def test_a_roboto_reference_draws_in_the_installs_truetype_file_and_other_faces_stand_in_with_a_reason():
    fonts = fonts2205.Fonts2205(_roboto_install())
    light = fonts.choose(_ref("Roboto_Light_Default.ffd", "Roboto Light"))
    assert (light.family, light.weight, light.real) == ("Roboto Test Face", 300, True)
    assert fonts.choose(_ref("Roboto_Default.ffd", "Roboto")).real
    medium = fonts.choose(_ref("Roboto_Medium_Default.ffd", "Roboto Medium"))
    assert (medium.family, medium.weight, medium.real) == ("Roboto Test Face", 500, False)
    arial = fonts.choose(_ref("Arial_Default.ffd", "Arial"))
    assert (arial.family, arial.real) == ("Arial", False) and fonts.choose(None).family == "Arial"
    notes = fonts.notes()
    assert any("Roboto Medium" in n for n in notes) and any("Arial" in n for n in notes)


def test_without_the_truetype_files_every_roboto_reference_stands_in_and_the_install_is_blamed():
    fonts = fonts2205.Fonts2205(_roboto_install(ttf=False))
    assert not fonts.choose(_ref("Roboto_Default.ffd", "Roboto")).real
    assert any("roboto-regular.ttf" in n for n in fonts.notes())


def test_the_preview_counts_its_texts_by_what_they_were_drawn_with():
    movie = feuread.read(_empty_movie())
    fonts = fonts2205.Fonts2205(_roboto_install())
    plain = uirender.Renderer(movie)
    assert plain.text_font(_ref("Roboto_Default.ffd", "Roboto"), 12).family() == "Roboto"  # the name only
    game = uirender.Renderer(movie, None, fonts.choose)
    font = game.text_font(_ref("Roboto_Light_Default.ffd", "Roboto Light"), 12)
    game.text_font(_ref("Arial_Default.ffd", "Arial"), 12)
    assert font.family() == "Roboto Test Face" and font.weight() == 300 and font.pixelSize() == 12
    assert (game.texts_in_game_font, game.texts_in_stand_in) == (1, 1)


def _empty_movie() -> bytes:
    from annotoolbox.formats import feuwrite
    from annotoolbox.formats.feuwrite import encode_rect

    frame = encode_rect((0, 400, 0, 200)) + struct.pack("<HH", 30 << 8, 1)
    end = [feuread.Tag(feuread.SHOW_FRAME, b""), feuread.Tag(feuread.END, b"")]
    return feuwrite.write(feuread.Movie(feuread.SIGNATURE, 12, frame, end))


class Chain:
    """What ``FontChain`` answers for 117: a headline and a body style, each with the files it tries."""

    available = True

    def styles(self, language):
        return [
            fontchain.StyleFaces("Body", ("data/fonts/body.otf",), 30, "k-body"),
            fontchain.StyleFaces("Head", ("data/fonts/head.otf",), 5, "k-head"),
        ]


def test_the_texts_app_shows_a_text_in_the_game_font_of_its_style_and_says_why_it_cannot():
    tree = Tree({"data/fonts/head.otf": make_ttf("Head Face"), "data/fonts/body.otf": make_ttf("Body Face")})
    check = fontcover.FontCheck(tree, "anno117", Chain())
    faces = textfont.Faces(tree, check, "anno117")
    assert faces.family("german")[0] == "Body Face"  # the most used style
    assert faces.family("german", "k-head") == ("Head Face", "")
    assert faces.family("german", "*")[0] == "Body Face"
    assert faces.family("german", "", ("k-head",))[0] == "Head Face"  # the style of the labels that show the text
    lost = textfont.Faces(Tree({}), fontcover.FontCheck(Tree({}), "anno117", Chain()), "anno117").family("german")
    assert lost[0] is None and lost[1]  # nothing of the styles is there: the plain font, with a reason


def test_a_game_without_text_styles_shows_its_default_font():
    tree = Tree({"data/loca/eng/fonts/georgia.ttf": make_ttf("Georgia Test Face")})
    faces = textfont.Faces(tree, fontcover.FontCheck(tree, "anno1404"), "anno1404")
    assert faces.family("english") == ("Georgia Test Face", "")
    tree.glob = None  # the file was looked up once: a row shown later does not search the game's files again
    assert faces.family("english") == ("Georgia Test Face", "")
    tree = Tree({"data/loca/eng/fonts/georgia.ttf": make_ttf("Georgia Test Face")})
    other = textfont.Faces(tree, fontcover.FontCheck(tree, "anno2070"), "anno2070").family("english")
    assert other[0] is None and other[1]  # no DejaVu there: the plain font, with the reason


def test_the_detail_panel_sets_the_game_font_on_both_boxes_and_the_reason_when_there_is_none():
    from annotoolbox.textedit.detailpanel import Actions, DetailPanel, RowView

    nothing = Actions(lambda _t: None, lambda: None, lambda: None, lambda _w: None)
    panel = DetailPanel(nothing)
    plain = panel.target_box.font().family()
    family = gamefonts.load(make_ttf("Panel Face"))
    panel.show_row(RowView(key="1", source="A", target="A", face=lambda style: (family, "")))
    assert panel.source_box.font().family() == panel.target_box.font().family() == "Panel Face"
    panel.show_row(RowView(key="1", source="A", target="A", face=lambda style: (None, "no such font")))
    assert panel.target_box.font().family() == plain and panel.target_box.toolTip() == "no such font"


LOCA_1404 = "<Localisation><GameFont><FontFilename>fonts/GEORGIA.ttf</FontFilename></GameFont>{fonts}</Localisation>"
FONT_1404 = (
    "<Font><Name>Default</Name><RelativeFontSize>18</RelativeFontSize><Weight>700</Weight>"
    "<FontName>Times New Roman</FontName></Font>"
)
FONT_1701 = (
    "<Font><Name>ToolTip</Name><Name_1024>Arial</Name_1024><Height_1024>16</Height_1024><Weight_1024>0</Weight_1024>"
    "<Name_768>Arial</Name_768></Font><Font><Name>Console</Name><FontSize>14</FontSize><FontName>Courier New</FontName></Font>"
)


def test_the_font_tables_of_1404_and_1701_are_read_from_their_localisation_files_even_when_not_well_formed():
    files = {
        "data/loca/eng/localisation.xml": LOCA_1404.format(fonts=FONT_1404).encode(),
        "data/loca/ger/localisation.xml": b"<x/>",
    }
    assert fontsloca.read(files, files.__getitem__) == [("Default", "Times New Roman", 18.0, 700)]
    broken = {"data/loca/eng/txt/localisation.xml": ("<a>" + FONT_1701 + "<unclosed>").encode()}
    assert fontsloca.read(broken, broken.__getitem__) == [
        ("ToolTip", "Arial", 16.0, 400),
        ("Console", "Courier New", 14.0, 400),
    ]
    assert fontsloca.read({"data/other.xml": b""}, {}.__getitem__) == []


@pytest.mark.skipif(not os.environ.get("ANNO2205_DIR"), reason="needs an installed Anno 2205 (ANNO2205_DIR)")
def test_the_installed_2205_descriptors_read_and_its_roboto_references_draw_in_the_shipped_files():
    from pathlib import Path

    from annotoolbox.othergames import installs, ui2205

    ui = ui2205.UiFiles(installs.GAMES["anno2205"].tree(Path(os.environ["ANNO2205_DIR"])))
    read = {}
    for path in ui.paths(".ffd"):
        try:
            read[path] = ffdread.read(ui.file(path))
        except ValueError:
            read[path] = None  # the bold descriptor is a stub
    assert sum(d is not None for d in read.values()) == 6 and read["data/ui/roboto_bold_default.ffd"] is None
    assert read["data/ui/roboto_light_default.ffd"].family == "Roboto Light"
    fonts = fonts2205.Fonts2205(ui)
    assert fonts.choose(_ref("Roboto_Light_Default.ffd", "Roboto Light")).real
    assert not fonts.choose(_ref("Roboto_Thin_Default.ffd", "Roboto Thin")).real
