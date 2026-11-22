"""The asset editor's pickers (assetedit/picker.py): the assets a reference may take, colours as the games write them,
the files a file name value may take, and the form's buttons."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QToolButton
from tests.sources.sourcesfakes import asset_game

from annotoolbox.assetedit import assetform, picker
from annotoolbox.assetedit.assetform import CAssetForm
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.schema import SValueSpec


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc(tmp_path):
    return open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache").document()


def test_candidates_respect_the_allowed_templates_the_needed_property_and_the_search(doc):
    everything = picker.candidates(doc, SValueSpec("Ref", "Asset"))
    assert {1001, 2001} <= set(everything)
    template = doc.template(2001)
    only = picker.candidates(doc, SValueSpec("Ref", "Asset", allowed_templates=(template,)))
    assert 2001 in only and all(doc.template(g) == template for g in only) and len(only) < len(everything)
    needs = doc.layers(2001).property_names()[0]
    assert 2001 in picker.candidates(doc, SValueSpec("Ref", "Asset", needed_property=f"Nope;{needs}"))
    assert picker.candidates(doc, SValueSpec("Ref", "Asset", needed_property="Nope")) == []
    assert picker.candidates(doc, SValueSpec("Ref", "Asset"), "2001")[0] == 2001
    assert picker.candidates(doc, SValueSpec("Ref", "Asset"), "no such asset") == []
    new = doc.new_asset("", "Wood 2", copy_of=1001)
    assert picker.candidates(doc, SValueSpec("Ref", "Asset"))[0] == new  # the change set's new assets first


def test_a_colour_is_the_signed_argb_number_of_the_games_files():
    white = picker.from_argb("-1")
    assert (white.red(), white.green(), white.blue(), white.alpha()) == (255, 255, 255, 255)
    veil = picker.from_argb("553648127")  # 0x20FFFFFF, a translucent white of 117's own data
    assert (veil.red(), veil.alpha()) == (255, 0x20)
    for value in ("-1", "553648127", "-5552570", "1272502488", "0"):
        assert picker.to_argb(picker.from_argb(value)) == value
    assert picker.from_argb("not a number").alpha() == 255


def test_files_follow_the_values_filter():
    paths = ["data/ui/a.png", "data/ui/b.dds", "data/ui/sub/c.PNG", "data/x.xml"]
    assert picker.files_for(paths, "*.png") == ["data/ui/a.png", "data/ui/sub/c.PNG"]
    assert picker.files_for(paths, "*.png;*.dds", "sub") == ["data/ui/sub/c.PNG"]
    assert picker.files_for(paths, "") == paths


def test_the_form_offers_a_picker_beside_a_reference_a_colour_and_a_file_name(qt, doc):
    form = CAssetForm()
    form.m_doc = doc
    spec = SValueSpec("Ref", "Asset", needed_property="Building")
    box = form._asset_buttons(QLineEdit("2001"), spec, ("A", "Ref"))
    assert [b.text() for b in box.findChildren(QToolButton)] == ["…", "→"]


def test_the_hint_lists_the_needed_properties_as_alternatives():
    assert picker.asset_hint(SValueSpec("Ref", "Asset", needed_property="A;B;C")) == "needs: A or B or C"
    assert picker.asset_hint(SValueSpec("Ref", "Asset", needed_property="A;")) == "needs: A"


def _list_spec() -> SValueSpec:
    return SValueSpec("BuildCost", "Vector", items=[SValueSpec("Product", "Asset"), SValueSpec("Amount", "Integer")])


def test_a_cell_edit_of_a_list_keeps_what_the_table_does_not_show(qt):
    """An item's nested block and a value the schema does not know stay when another cell of the list is edited."""
    block = ET.fromstring(
        "<BuildCost><Item><Product>1001</Product><Amount>2</Amount><Extra>kept</Extra></Item>"
        "<Item><Product><Inner>7</Inner></Product><Amount>3</Amount></Item></BuildCost>"
    )
    spec, cols = _list_spec(), ["Product", "Amount"]
    table = assetform._filled_table(["1", "2"], cols, block.findall("Item"))
    table.item(0, 1).setText("5")
    table.item(1, 1).setText("")
    out = assetform._rebuilt_block(spec, ("Building", "BuildCost"), block, table, ["1", "2"], cols)
    first, second = out.findall("Item")
    assert first.findtext("Amount") == "5" and first.findtext("Extra") == "kept"
    assert second.findtext("Product/Inner") == "7" and second.find("Amount") is None  # an emptied cell sets nothing
    assert block.findtext("Item/Amount") == "2"  # the element the table was filled from is not written to


def test_a_list_of_plain_items_is_edited_as_the_items_text(qt):
    """A list the schema gives no item values: the cell is the item's text, not a child named after the column."""
    block = ET.fromstring("<Tags><Item>a</Item><Item>b</Item></Tags>")
    spec, cols = SValueSpec("Tags", "Vector"), [assetform.PLAIN]
    table = assetform._filled_table(["1", "2"], cols, block.findall("Item"))
    assert [table.item(r, 0).text() for r in range(2)] == ["a", "b"]
    table.item(1, 0).setText("c")
    out = assetform._rebuilt_block(spec, ("Standard", "Tags"), block, table, ["1", "2"], cols)
    assert ET.tostring(out, encoding="unicode") == "<Tags><Item>a</Item><Item>c</Item></Tags>"


def test_text_typed_into_a_field_goes_to_its_own_asset_when_another_is_shown(qt, doc):
    from PySide6.QtTest import QTest

    form = CAssetForm()
    form.show()
    form.activateWindow()
    form.show_asset(doc, 2001)
    qt.processEvents()
    name = next(e for e in form.findChildren(QLineEdit) if e.text() == "Hut")
    name.setFocus()
    QTest.keyClicks(name, "X")  # typed, the field not left
    form.show_asset(doc, 2002)  # a jump, a link, a paste: the form is built for another asset
    qt.processEvents()
    assert doc.m_changes.m_assets[2001].set == {"Standard/Name": "HutX"} and 2002 not in doc.m_changes.m_assets
    form.close()


def test_a_read_only_value_cannot_be_typed_beside_its_picker(qt, doc):
    form = CAssetForm()
    form.show_asset(doc, 2001)
    for kind in ("Flags", "Asset", "Color", "String"):
        row = form.field(SValueSpec("Kinds", kind, read_only=True), ("Building", "Kinds"))
        line = row.findChild(QLineEdit)
        assert line.isReadOnly() or not line.isEnabled(), kind
    assert not form.field(SValueSpec("Kinds", "Flags"), ("Building", "Kinds")).findChild(QLineEdit).isReadOnly()


def test_an_asset_name_with_markup_shows_as_text_in_the_forms_head(qt, doc):
    doc.rename(2001, "<i>Hut</i> & Co")
    form = CAssetForm()
    form.show_asset(doc, 2001)
    head = form.widget().findChildren(QLabel)[0]
    assert head.text().startswith("<b>&lt;i&gt;Hut&lt;/i&gt; &amp; Co</b>")
