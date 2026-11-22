"""The editor's own descriptions of 117's properties (assetedit/descriptions.py, data/descriptions-anno117.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tests.fakes import installed
from tests.sources.sourcesfakes import ASSET_SCHEMA, asset_game

from annotoolbox.assetedit import descriptions
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.schema import read_schema


def schema():
    return read_schema("anno117", [ASSET_SCHEMA.encode()], None, None)


def test_the_table_is_filled_only_where_the_schema_has_no_description():
    sch = schema()
    sch.properties["Building"].child("Upkeep").description = "the game's own"
    table = {
        "Building": "A building.",
        "Building/Upkeep": "Mine.",
        "Building/BuildCost/Amount": "How many.",
        "Building/Nowhere": "No such value.",
        "Nothing": "No such property.",
    }
    assert descriptions.unresolved(sch, table) == ["Building/Nowhere", "Nothing"]
    assert descriptions.apply(sch, table) == 2
    building = sch.properties["Building"]
    assert building.description == "A building." and building.written
    assert building.child("Upkeep").description == "the game's own" and not building.child("Upkeep").written
    amount = building.child("BuildCost").child("Amount")
    assert amount.description == "How many." and amount.written
    assert descriptions.apply(sch, table) == 0  # a second time changes nothing


def test_a_missing_or_damaged_table_is_empty(tmp_path):
    assert descriptions.load("anno1800", tmp_path) == {}
    (tmp_path / "descriptions-x.json").write_text("{not json", encoding="utf-8")
    assert descriptions.load("x", tmp_path) == {}
    (tmp_path / "descriptions-y.json").write_text(json.dumps({"descriptions": {"A": "a", "B": "", "C": 3}}))
    assert descriptions.load("y", tmp_path) == {"A": "a"}


def test_the_shipped_table_is_short_plain_sentences():
    table = descriptions.load("anno117")
    assert len(table) > 500
    for key, text in table.items():
        assert text.endswith(".") and len(text) <= 160, key
        assert key and not key.endswith("/") and "  " not in text, key
    assert "Building/Costs/Amount" not in table and "Cost/Costs/Amount" in table


def test_a_game_gets_its_own_descriptions_after_the_donors(tmp_path):
    donor = asset_game(tmp_path / "1800", donor=True)  # describes Building/Upkeep itself
    game = open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache", donor=donor)
    building = game.m_schema.properties["Building"]
    assert building.child("Upkeep").description == "Running cost a minute" and building.child("Upkeep").borrowed
    assert building.child("Upkeep").written is False  # the donor's description comes first
    assert building.description == descriptions.load("anno117")["Building"] and building.written
    assert any("written for this editor" in n for n in game.m_notes)


@pytest.mark.skipif(installed("anno117") is None, reason="needs an Anno 117 install (ANNO117_DIR)")
def test_every_key_names_a_property_or_value_of_the_real_schema(tmp_path):
    game = open_game("anno117", Path(os.environ["ANNO117_DIR"]), tmp_path)
    assert descriptions.unresolved(game.m_schema, descriptions.load("anno117")) == []
    assert any("written for this editor" in n for n in game.m_notes)
    assert game.m_schema.properties["Cost"].child("Costs").written
