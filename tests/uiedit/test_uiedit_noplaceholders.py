"""What the preview takes from the game's files instead of a placeholder: a label shows the line its text id names
(also when a binding gave it a filler, and in 1800's decimal ids), a text property a file feeds into a picture holds a
picture's name and not a text, and a game object is never shown as a text."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

from tests.ui.uifakes import SCHEMA
from tests.uiedit.uieditfakes import GEN, IMAGE, image_file, scene_instance
from tests.uiedit.uieditfakes import UIEDIT_VERSIONS as VERSIONS
from tests.uiedit.uieditfakes import ui_file_bytes as _write

from annotoolbox.formats import phoenix, phoenixedit
from annotoolbox.sources import gametree
from annotoolbox.uiedit import bindings, labels, layout, placeholders, scope, uifiles
from annotoolbox.uiedit.scope import SGameObjectRef

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
LOCA = "A010 D068 1398 4305"
PICTURE_FILE = str(uuid.uuid5(uuid.NAMESPACE_URL, "noplaceholders-picture"))
HOLDER_FILE = str(uuid.uuid5(uuid.NAMESPACE_URL, "noplaceholders-holder"))


def _ctx(lines: dict[str, str]):
    files = SimpleNamespace(text_of=lambda loca, language="english": lines.get(loca))
    return SimpleNamespace(m_live_text={}, m_files=files, m_language="english")


def _label(text: str = "", loca: str = "") -> dict:
    node = scene_instance("41a404e5", "Caption")
    if text:
        phoenixedit.set_field(node, "Text", text)
    if loca:
        phoenixedit.set_field(node, "LocalizationId", loca)
    return node


def test_a_label_with_a_text_id_shows_its_line_not_the_stored_text():
    ctx = _ctx({LOCA: "Tom & Jerry"})
    assert labels.label_text(ctx, _label("!Headline", LOCA), {}) == "Tom & Jerry"
    assert labels.label_text(ctx, _label("!Headline"), {}) == "!Headline"  # no id: the text as stored
    assert labels.label_text(ctx, _label(loca="0000 0000 0000 0001"), {}) == "[0000 0000 0000 0001]"


def test_a_filler_gives_way_to_the_label_own_line_but_a_real_value_does_not():
    ctx = _ctx({LOCA: "Tom & Jerry"})
    filler = placeholders.TEXTS["medium"]
    assert labels.label_text(ctx, _label(loca=LOCA), {(labels.TEXT,): filler}) == "Tom & Jerry"
    assert labels.label_text(ctx, _label(loca=LOCA), {(labels.TEXT,): "Marcia"}) == "Marcia"
    assert labels.label_text(ctx, _label(), {(labels.TEXT,): filler}) == filler  # nothing of its own: the filler


def test_a_game_object_is_no_text():
    ctx = _ctx({})
    assert labels.label_text(ctx, _label(), {(labels.TEXT,): SGameObjectRef("scene/10000002")}) == ""
    assert labels.label_text(ctx, _label(), {(labels.TEXT,): 12}) == "12"


def test_1800s_decimal_text_ids_read_its_guid_tables(tmp_path):
    game = tmp_path / "game"
    table = game / "data/config/gui/texts_english.xml"
    table.parent.mkdir(parents=True)
    table.write_text("<TextExport><Texts><Text><GUID>5998</GUID><Text>Score</Text></Text></Texts></TextExport>")
    files = uifiles.CUiFiles(gametree.loose_tree(game))
    assert files.text_of("5998") == "Score" and files.text_of("5999") is None
    line = int("A010D06813984305", 16) - (1 << 64)  # 117's ids stay 16 hex digits, also written without spaces
    files._m_texts["english"][line] = "Tom & Jerry"
    assert files.text_of(LOCA) == files.text_of(LOCA.replace(" ", "")) == "Tom & Jerry"


def _picture_files(tmp_path):
    """A widget file whose ``Icon`` text property is the picture's ``DynamicImageId``, one that hands its own
    ``Name`` property into it, and one text property nothing feeds into a picture."""
    inner = phoenixedit.new_object(
        SCHEMA, phoenixedit.WIDGET_TEMPLATE, guid=uuid.UUID(PICTURE_FILE).bytes_le, root=True, versions=VERSIONS
    )
    picture = scene_instance("c19f99dd", "Picture")
    wf = phoenixedit.CWidgetFile(SCHEMA, inner, versions=VERSIONS)
    wf.add_instance(picture)
    wf.add_property("Icon", "string", "")
    wf.add_property("Title", "string", "")
    wf.bind(wf.own("Icon"), wf.end(picture, [phoenixedit.BUILTIN["DynamicImageId"]]))
    wf.finish("inner")
    outer = phoenixedit.new_object(
        SCHEMA, phoenixedit.WIDGET_TEMPLATE, guid=uuid.UUID(HOLDER_FILE).bytes_le, root=True, versions=VERSIONS
    )
    box = scene_instance("0f561333", "Slot")
    phoenixedit.set_raw(box, "Template", phoenix.ref_bytes(phoenixedit.WIDGET_TEMPLATE, PICTURE_FILE))
    wf2 = phoenixedit.CWidgetFile(SCHEMA, outer, versions=VERSIONS)
    wf2.add_instance(box)
    wf2.add_property("Name", "string", "")
    wf2.bind(wf2.own("Name"), wf2.end(box, [wf.m_unique_ids["Icon"]]))
    wf2.finish("outer")
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    (gen / PICTURE_FILE).write_bytes(_write("WI", inner))
    (gen / HOLDER_FILE).write_bytes(_write("WI", outer))
    (gen / IMAGE).write_bytes(image_file())
    return uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game")), wf, wf2


def test_a_text_property_a_file_feeds_into_a_picture_holds_a_picture_name(tmp_path):
    files, inner, outer = _picture_files(tmp_path)
    ctx = layout.CContext(files)
    assert scope.feeds_picture(ctx, files.root(PICTURE_FILE), inner.m_unique_ids["Icon"])
    assert not scope.feeds_picture(ctx, files.root(PICTURE_FILE), inner.m_unique_ids["Title"])
    # a file that hands its property on into the placed file's picture feeds a picture too
    assert scope.feeds_picture(ctx, files.root(HOLDER_FILE), outer.m_unique_ids["Name"])


def test_a_filler_follows_the_name_of_what_it_stands_for():
    assert placeholders.text_for("Amount") == "250" and placeholders.text_for("PlayerCount") == "12"
    assert placeholders.text_for("Description") == placeholders.TEXTS["long"]
    assert placeholders.text_for("ButtonText") == placeholders.TEXTS["short"]
    assert placeholders.is_picture_name("Icon") and placeholders.is_picture_name("Model2D")
    assert not placeholders.is_picture_name("IconText") and not placeholders.is_picture_name("Name")


def test_a_text_property_named_like_a_picture_gets_a_picture_slot():
    source = bindings.SEnd(0, "path", {}, [(1, None)], [0x10000001, 0x20000002])
    target = bindings.SEnd(1, "own", {}, [], [0x10000003])
    names = {0x20000002: ("value", "Icon"), 0x10000003: ("string", "Icon")}

    def describe(end):
        return names.get(end.ids[-1], ("value", ""))

    assign = bindings.SBinding({}, "c346a1bd", [source], [target])
    assert placeholders.guess(assign, 0, describe) == placeholders.PICTURE + "Icon"
    names[0x20000002] = ("value", "Amount")
    names[0x10000003] = ("string", "Amount")
    assert placeholders.guess(assign, 0, describe) == "250"
