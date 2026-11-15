"""The asset editor's checks beyond references and numbers: ids of scripts and text groups, the allowed properties of
a reference, variable values and a template the game no longer has."""

from __future__ import annotations

import pytest
from tests.fakes import asset_game

from annotoolbox.assetedit import checks
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.schema import ValueSpec


@pytest.fixture
def doc(tmp_path):
    return open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache").document()


def messages(found):
    return [f.message for f in found]


@pytest.mark.parametrize("kind", ["ScriptId", "TextGroup", "ScriptIdGroup"])
def test_a_script_id_or_text_group_is_a_whole_number_of_64_bits(doc, kind):
    spec = ValueSpec("AudioText", kind)
    assert checks._value(doc, 1, ("X", "AudioText"), "-6912837866936317073", spec) == []
    assert checks._value(doc, 1, ("X", "AudioText"), "", spec) == []
    assert "no id" in messages(checks._value(doc, 1, ("X", "AudioText"), "pool_a", spec))[0]
    assert checks._value(doc, 1, ("X", "AudioText"), "1" + "0" * 30, spec)


def test_a_reference_must_have_one_of_the_allowed_properties(doc):
    spec = ValueSpec("Ref", "Asset", allowed_properties=("Nothing", "Neither"))
    found = checks._value(doc, 1, ("X", "Ref"), "2001", spec)
    assert [f.severity for f in found] == [checks.WARNING] and "none of the properties" in found[0].message
    has = doc.layers(2001).property_names()[0]
    assert checks._value(doc, 1, ("X", "Ref"), "2001", ValueSpec("Ref", "Asset", allowed_properties=(has,))) == []


def test_a_variable_value_names_a_variable(doc):
    named = {("T", "Init", "Value"): "ActiveEmperor", ("T", "Init", "IsVariable"): "1"}
    assert checks._variables(doc, 1, named) == []
    for value in ("", "42"):
        found = checks._variables(doc, 1, {**named, ("T", "Init", "Value"): value})
        assert len(found) == 1 and "no variable name" in found[0].message
    assert checks._variables(doc, 1, {("T", "Init", "Value"): "42", ("T", "Init", "IsVariable"): "0"}) == []


def test_an_asset_of_a_template_the_game_no_longer_has_is_said(doc):
    guid = doc.new_asset("Mission", "Lost")
    assert not [f for f in checks.check_asset(doc, guid) if f.path == "Template"]
    doc.changes.new[guid].template = "Removed"
    doc._parsed.pop(guid, None)
    found = [f for f in checks.check_asset(doc, guid) if f.path == "Template"]
    assert found and "Removed" in found[0].message
