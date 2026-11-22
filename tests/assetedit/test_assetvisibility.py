"""Which values the asset form shows: 1800's visibility conditions, the values a template calls secondary, and the
game's own value beside an edited one (assetedit/visibility.py, assetform.py)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from tests.fakes import installed
from tests.sources.sourcesfakes import ASSET_SCHEMA, ASSET_TEMPLATES, asset_game

from annotoolbox.assetedit import visibility
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.schema import read_schema
from annotoolbox.assetedit.visibility import SCondition, holds
from annotoolbox.assetedit.xmltree import parse

CONDITIONS = (
    "<ValueVisibilityConditions><Building>"
    "<Upkeep><Item><Name>Condition</Name><ValueDefinition>Level</ValueDefinition>"
    "<ValueConstraintType>CONSTRAINT_EQUALS</ValueConstraintType><ValueConstraint>High</ValueConstraint></Item></Upkeep>"
    "<Kinds><Item><Name>Condition</Name><ValueDefinition>Level</ValueDefinition>"
    "<ValueConstraintType>CONSTRAINT_CHOICE_FLAGSET</ValueConstraintType><ValueConstraint>Low;High</ValueConstraint>"
    "</Item><Item><Name>Condition</Name><ValueDefinition>Upkeep</ValueDefinition>"
    "<ValueConstraintType>CONSTRAINT_OVERRIDDEN</ValueConstraintType></Item></Kinds>"
    "</Building></ValueVisibilityConditions>"
)


def schema_with(conditions: str = CONDITIONS, templates: str = ASSET_TEMPLATES):
    props = ASSET_SCHEMA.replace("</Properties>", conditions + "</Properties>")
    return read_schema("anno1800", [props.encode()], templates.encode(), None)


def test_the_conditions_and_hidden_values_are_read_from_the_schema_files():
    hidden = ASSET_TEMPLATES.replace(
        "<Properties><Standard /><Building>",
        "<HiddenValues><Upkeep /><Model /></HiddenValues><Properties><Standard /><Building>",
        1,
    )
    schema = schema_with(templates=hidden)
    assert schema.conditions[("Building", "Upkeep")] == (SCondition("Level", "equals", "High"),)
    assert schema.conditions[("Building", "Kinds")] == (SCondition("Level", "flagset", "Low;High"),
                                                        SCondition("Upkeep", "overridden"))  # fmt: skip
    assert schema.templates["House"].hidden == ("Upkeep", "Model") and schema.templates["Good"].hidden == ()
    assert read_schema("anno117", [ASSET_SCHEMA.encode()], None, None).conditions == {}


@pytest.mark.parametrize(
    ("cond", "current", "overridden", "first", "expected"),
    [
        (SCondition("A", "equals", "1"), "1", False, "", True),
        (SCondition("A", "equals", "1"), "0", False, "", False),
        (SCondition("A", "equals", "1"), "1.0", False, "", True),  # numbers compare as numbers
        (SCondition("A", "equals", "1"), "", False, "", False),  # nothing set: off
        (SCondition("A", "equals", "0"), "", False, "", True),
        (SCondition("A", "equals"), "", False, "", True),  # no operand: the value is off
        (SCondition("A", "equals"), "1", False, "", False),
        (SCondition("A", "equals", "Area"), "Area", True, "", True),
        (SCondition("A", "min", "5"), "5", False, "", True),
        (SCondition("A", "min", "5"), "4", False, "", False),
        (SCondition("A", "min", "99999"), "", False, "", False),  # the "exists" trick of 1800's data
        (SCondition("A", "flagset", "Coach;Train"), "Train;Other", False, "", True),
        (SCondition("A", "flagset", "Coach;Train"), "", False, "Coach", True),  # a choice nobody set is its first item
        (SCondition("A", "flagset", "Coach;Train"), "Bus", False, "", False),
        (SCondition("A", "overridden"), "3", True, "", True),
        (SCondition("A", "overridden"), "3", False, "", False),
        (SCondition("A", "default"), "", False, "", True),
    ],
)
def test_a_condition_holds_by_its_kind(cond, current, overridden, first, expected):
    assert holds(cond, current, overridden, first) is expected


def test_all_conditions_of_a_value_must_hold():
    both = (SCondition("A", "equals", "1"), SCondition("B", "min", "2"))
    answers = {"A": ("1", True, ""), "B": ("3", False, "")}
    assert visibility.visible(both, answers.__getitem__)
    answers["B"] = ("1", False, "")
    assert not visibility.visible(both, answers.__getitem__)


def test_a_missing_condition_file_part_is_harmless():
    assert visibility.read_conditions(parse("<Properties />")) == {}
    assert visibility.read_hidden(parse("<Template><Name>X</Name></Template>")) == ()


@pytest.fixture
def doc(tmp_path):
    root = asset_game(tmp_path / "117")
    export = root / "data/base/config/export/"
    text = (export / "properties-meta.xml").read_text(encoding="utf-8")
    (export / "properties-meta.xml").write_text(
        text.replace("</Properties>", CONDITIONS + "</Properties>"), encoding="utf-8"
    )
    tpl = (export / "templates.xml").read_text(encoding="utf-8")
    (export / "templates.xml").write_text(
        tpl.replace("<Properties><Standard /><Building><Level>", "<HiddenValues><Workers /></HiddenValues>"
                    "<Properties><Standard /><Building><Level>", 1), encoding="utf-8")  # fmt: skip
    return open_game("anno117", root, tmp_path / "cache").document()


def labels(form, block: str = "Building") -> list[str]:
    from PySide6.QtWidgets import QLabel

    form.m_blocks[block].set_open(True)
    return [lab.text().replace("<b>", "").replace("</b>", "") for lab in form.m_blocks[block].findChildren(QLabel)]


@pytest.fixture(scope="module")
def qt():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_form_leaves_out_values_whose_conditions_fail_and_the_templates_secondary_values(qt, doc):
    from annotoolbox.assetedit.assetform import CAssetForm

    form = CAssetForm()
    form.show_asset(doc, 2001)  # a Low house: Upkeep needs High, Kinds needs Upkeep set; Workers is secondary
    shown = labels(form)
    assert "Upkeep" not in shown and "Kinds" not in shown and "Workers" not in shown
    assert any("not shown" in t and "secondary" in t for t in shown)
    doc.set_value(2001, "Building/Level", "High")
    form.show_asset(doc, 2001)
    assert "Upkeep" in labels(form) and "Kinds" not in labels(form)  # Upkeep is shown but nobody set it
    doc.set_value(2001, "Building/Upkeep", "7")
    form.show_asset(doc, 2001)
    assert "Kinds" in labels(form)
    form.m_secondary = form.m_conditional = True
    form.show_asset(doc, 2001)
    assert {"Workers", "Upkeep", "Kinds"} <= set(labels(form))
    assert not any("not shown" in t for t in labels(form))


def test_the_original_value_stands_beside_an_edited_one(qt, doc):
    from annotoolbox.assetedit.assetform import CAssetForm

    form = CAssetForm()
    form.m_original = form.m_conditional = True
    doc.set_value(2002, "Building/Upkeep", "12")  # 2002 sets 9 itself
    doc.set_value(2001, "Building/Level", "High")  # 2001 gets the template's Low from the game
    form.show_asset(doc, 2002)
    assert "game: 9" in labels(form)
    form.show_asset(doc, 2001)
    assert "game: Low" in labels(form) and "game: 9" not in labels(form)
    assert doc.layers(2001, original=True).lookup(("Building", "Level")).text == "Low"
    assert doc.layers(2001).lookup(("Building", "Level")).text == "High"
    new = doc.new_asset("", "Copy", copy_of=2001)
    assert doc.layers(new, original=True).lookup(("Building", "Level")).text == "High"  # no original: its own


@pytest.mark.skipif(installed("anno1800") is None, reason="needs an Anno 1800 install (ANNO1800_DIR)")
def test_the_conditions_of_the_real_1800_schema_name_values_it_defines(tmp_path):
    import os
    from pathlib import Path

    from annotoolbox.assetedit.assetgame import read_schema_of
    from annotoolbox.assetedit.gamefiles import LAYOUTS, open_files

    files = open_files("anno1800", Path(os.environ["ANNO1800_DIR"]), tmp_path)
    schema = read_schema_of(LAYOUTS["anno1800"], files)
    assert len(schema.conditions) > 200
    assert all(schema.properties[p].child(v) is not None for (p, v) in schema.conditions)
