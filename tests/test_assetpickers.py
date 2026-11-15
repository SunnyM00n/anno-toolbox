"""The asset editor's pickers (assetedit/picker.py): the assets a reference may take, colours as the games write them,
the files a file name value may take, and the form's buttons."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLineEdit, QToolButton
from tests.fakes import asset_game

from annotoolbox.assetedit import picker
from annotoolbox.assetedit.assetform import AssetForm
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.schema import ValueSpec


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc(tmp_path):
    return open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache").document()


def test_candidates_respect_the_allowed_templates_the_needed_property_and_the_search(doc):
    everything = picker.candidates(doc, ValueSpec("Ref", "Asset"))
    assert {1001, 2001} <= set(everything)
    template = doc.template(2001)
    only = picker.candidates(doc, ValueSpec("Ref", "Asset", allowed_templates=(template,)))
    assert 2001 in only and all(doc.template(g) == template for g in only) and len(only) < len(everything)
    needs = doc.layers(2001).property_names()[0]
    assert 2001 in picker.candidates(doc, ValueSpec("Ref", "Asset", needed_property=f"Nope;{needs}"))
    assert picker.candidates(doc, ValueSpec("Ref", "Asset", needed_property="Nope")) == []
    assert picker.candidates(doc, ValueSpec("Ref", "Asset"), "2001")[0] == 2001
    assert picker.candidates(doc, ValueSpec("Ref", "Asset"), "no such asset") == []
    new = doc.new_asset("", "Wood 2", copy_of=1001)
    assert picker.candidates(doc, ValueSpec("Ref", "Asset"))[0] == new  # the change set's new assets first


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
    form = AssetForm()
    form.doc = doc
    spec = ValueSpec("Ref", "Asset", needed_property="Building")
    box = form._asset_buttons(QLineEdit("2001"), spec, ("A", "Ref"))
    assert [b.text() for b in box.findChildren(QToolButton)] == ["…", "→"]


def test_the_hint_lists_the_needed_properties_as_alternatives():
    assert picker.asset_hint(ValueSpec("Ref", "Asset", needed_property="A;B;C")) == "needs: A or B or C"
    assert picker.asset_hint(ValueSpec("Ref", "Asset", needed_property="A;")) == "needs: A"
