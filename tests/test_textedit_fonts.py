"""The Texts app's glyph check (docs/text-editor.md, "Beyond the text", "Text styles of a text", "1800's font chain"):
a font's character table, the faces a text style tries per language, the labels that link a text to its styles. Synthetic
font files; the installs are in test_textedit_installed."""

from __future__ import annotations

import struct
import uuid
from types import SimpleNamespace

import pytest

from annotoolbox.textedit import fontchain, fontcover, stylelinks
from test_textedit import GERMAN_117, XML_117, _doc, _write


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


# the style of a text
def test_a_label_names_its_text_by_hex_digits_in_117_and_a_decimal_guid_in_1800():
    assert stylelinks.text_id("A018 4AB6 6641 96B8") == str(0xA0184AB6664196B8 - (1 << 64))
    assert stylelinks.text_id("0001 0000 0000 0001") == str(0x0001000000000001)
    assert stylelinks.text_id("22537") == "22537" and stylelinks.text_id("") == "" and stylelinks.text_id("12") == "12"
    assert stylelinks.text_id("zzzz zzzz zzzz zzzz") == ""


class _KeyedChain:
    available = True

    def styles(self, language):
        head, body = "data/fonts/albertusnovaforanno117tab.otf", "data/fonts/lisboasans.otf"
        return [
            fontchain.StyleFaces("AlbertusNova", (head,), 20, "lf-head"),
            fontchain.StyleFaces("LisboaSans", (body,), 14, "lf-body"),
        ]


def test_a_text_is_checked_in_the_styles_of_its_labels_and_the_picker_can_name_another(tmp_path):
    files = _fonts117(tmp_path, latin=(0x20, 0x7E), body=(0x20, 0x5A))
    doc = _doc(files)
    doc.fonts = fontcover.FontCheck(files, "anno117", _KeyedChain())
    doc.links = SimpleNamespace(fonts_of=lambda key: ("lf-body",) if key == "-13" else (), load=lambda: None)
    doc.prepare("english", "german")
    assert doc.style_choices() == [("lf-head", "AlbertusNova"), ("lf-body", "LisboaSans")]
    assert [p.severity for p in doc.glyph_problems("u", key="-13")] == ["warning"]  # the label's style lacks it
    assert [p.severity for p in doc.glyph_problems("u", key="-10")] == ["hint"]  # no link: every style, one draws it
    assert doc.glyph_problems("u", "lf-head", "-13") == [] and [
        p.severity for p in doc.glyph_problems("u", "*", "-13")
    ] == ["hint"]
    assert doc.linked_names("-13") == ["LisboaSans"] and doc.linked_names("-10") == []


# 1800's font chain
def _ref(guid: str) -> bytes:
    return bytes([1]) + bytes.fromhex("36c9a85f") + uuid.UUID(guid).bytes_le


def test_a_localized_font_the_schema_does_not_decode_is_read_by_the_references_it_holds():
    ft, ft2, ec = (str(uuid.uuid4()) for _ in range(3))
    exception = bytes([2, 0]) + bytes.fromhex("95a87446") + bytes.fromhex("097f80") + bytes([6, 1]) + b"Korean"
    exception2 = bytes([2, 0]) + bytes.fromhex("95a87446") + bytes.fromhex("097f80") + bytes([7, 1]) + b"Chinese"
    data = b"PHXLF" + _ref(ft) + exception + _ref(ft2) + bytes(5) + exception2 + _ref(ec) + _ref(str(uuid.uuid4()))
    raw = fontchain.scan_localized(data, {ft, ft2, ec})
    assert raw.base == [ft] and raw.exceptions == {"korean": [ft2], "chinese": [ec]}  # a stray reference is no font
