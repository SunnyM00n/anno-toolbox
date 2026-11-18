"""The asset editor's data layer on a synthetic game (``fakes.asset_game``): the schema, the index, the document's
layers and edits, the two writers, the checks, the diff and usage, the table files; the checks beyond references and
numbers too (ids of scripts and text groups, a reference's allowed properties, variable values, a lost template)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from tests.fakes import asset_game

from annotoolbox.assetedit import assetindex, balancing, checks, compare, modops, native
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.changeset import ChangeSet
from annotoolbox.assetedit.schema import ValueSpec
from annotoolbox.assetedit.values import BASE, DEFAULT, OWN, TEMPLATE
from annotoolbox.assetedit.xmltree import find_path


@pytest.fixture
def game(tmp_path):
    return open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache")


@pytest.fixture
def doc(game):
    return game.document()


def test_the_schema_reads_definitions_defaults_templates_and_data_sets(game):
    s = game.schema
    assert set(s.properties) == {"Standard", "Building", "Product", "Quest"}
    cost = s.spec_at(("Building", "BuildCost"))
    assert cost.kind == "list" and [v.name for v in cost.items] == ["Product", "Amount"]
    assert s.spec_at(("Building", "BuildCost", "Item", "Amount")).maximum == 100
    assert s.spec_at(("Building", "Workers", "High", "Count")).kind == "number"  # an array item by data set name
    assert s.spec_at(("Building", "Level")).kind == "choice" and s.data_sets["Level"].names == ["Low", "High"]
    assert s.spec_at(("Building", "BuildCost", "Item", "Product")).needed_property == "Product"
    assert s.defaults["Building"].findtext("Upkeep") == "5"
    assert s.templates["House"].property_names == ["Standard", "Building"]


def test_a_game_borrows_descriptions_from_the_donors_schema(tmp_path):
    donor = asset_game(tmp_path / "1800", donor=True)
    game = open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache", donor=donor)
    upkeep = game.schema.spec_at(("Building", "Upkeep"))
    assert upkeep.description == "Running cost a minute" and upkeep.borrowed
    assert any("1 descriptions borrowed" in n for n in game.notes)


def test_the_index_finds_every_asset_its_group_and_the_references_and_is_cached(game, tmp_path):
    ix = game.index
    assert sorted(ix.by_guid) == [1001, 1002, 2001, 2002, 3001]  # the inline asset of the quest is a value
    assert ix.by_guid[2002].template == "House" and ix.by_guid[2002].base == 2001  # the base asset's template
    assert ix.groups[ix.by_guid[2001].group] == ("Buildings", "Houses")
    assert ix.references(1001) == [2001] and ix.references(1002) == [3001]
    assert ix.children(2001) == [2002]
    assert ix.element(2001).findtext("Values/Standard/Name") == "Hut"
    assert [e.guid for e in ix.search("hut")] == [2001, 2002] and ix.search("1002")[0].guid == 1002
    again = open_game("anno117", tmp_path / "117", tmp_path / "cache").index
    assert [(e.guid, e.start, e.end) for e in again.entries] == [(e.guid, e.start, e.end) for e in ix.entries]
    assert list((tmp_path / "cache" / "asset-index").glob("*.json.gz"))


def test_the_scan_takes_the_shorthand_close_and_skips_comments():
    data = b"<AssetList><!-- <Asset> --><Assets><Asset><Values><Standard><GUID>5</GUID><Name>A</></></></Asset>"
    data += b"</Assets></AssetList>"
    entries, _refs = assetindex.scan_file(data, 0, [], {})
    assert [(e.guid, e.name) for e in entries] == [(5, "A")]


def test_values_come_from_their_layers(doc):
    layers = doc.layers(2002)
    assert layers.lookup(("Building", "Upkeep")).origin == OWN
    assert layers.lookup(("Building", "Kinds")).origin == BASE and layers.lookup(("Building", "Kinds")).source == 2001
    assert (
        layers.lookup(("Building", "Level")).text == "Low" and layers.lookup(("Building", "Level")).origin == TEMPLATE
    )
    assert doc.layers(2001).lookup(("Building", "Upkeep")).origin == DEFAULT
    assert layers.property_names()[:2] == ["Standard", "Building"]


def test_edits_undo_redo_and_reset(doc):
    doc.set_value(2001, "Building/Upkeep", "7")
    assert doc.layers(2001).lookup(("Building", "Upkeep")).text == "7" and doc.dirty
    assert doc.layers(2002).lookup(("Building", "Upkeep")).text == "9"  # its own still wins
    doc.reset_value(2002, "Building/Upkeep")
    assert doc.layers(2002).lookup(("Building", "Upkeep")).text == "7"  # now the base's edited value
    assert doc.changes.assets[2002].reset == ["Building/Upkeep"]
    doc.undo.undo()
    assert doc.layers(2002).lookup(("Building", "Upkeep")).text == "9"
    doc.undo.undo()
    assert 2001 not in doc.changes.assets
    doc.undo.redo()
    assert doc.changes.assets[2001].set == {"Building/Upkeep": "7"}


def test_a_list_is_replaced_whole_and_a_value_inside_it_folds_in(doc):
    cost = ET.fromstring("<BuildCost><Item><Product>1001</Product><Amount>4</Amount></Item>"
                         "<Item><Product>1002</Product><Amount>2</Amount></Item></BuildCost>")  # fmt: skip
    doc.set_block(2001, "Building/BuildCost", cost)
    doc.set_value(2001, "Building/BuildCost/Item/Amount", "6")
    items = find_path(doc.asset(2001), "Values/Building/BuildCost").findall("Item")
    assert [i.findtext("Amount") for i in items] == ["6", "2"] and not doc.changes.assets[2001].set


def test_new_assets_from_a_template_a_base_asset_and_as_a_copy(doc):
    a = doc.new_asset("House", "Villa")
    assert a == doc.changes.guid_range[0]
    assert [c.tag for c in doc.asset(a).find("Values")] == ["Standard", "Building"]
    b = doc.new_asset("", "Big Villa", base=a)
    assert doc.asset(b).findtext("BaseAssetGUID") == str(a) and doc.template(b) == "House"
    c = doc.new_asset("", "Wood 2", copy_of=1001)
    assert doc.asset(c).findtext("Values/Product/Price") == "12" and doc.asset(c).findtext(
        "Values/Standard/GUID"
    ) == str(c)
    assert doc.changes.new[c].anchor == 1001 and doc.changes.new[c].template == "Good"
    doc.rename(c, "Planks")
    assert doc.name(c) == "Planks"
    doc.remove_asset(c)
    assert c not in doc.changes.new
    doc.undo.undo()
    assert doc.changes.new[c].name == "Planks"


def test_the_change_set_file_round_trips(doc, tmp_path):
    doc.set_value(2001, "Building/Upkeep", "7")
    doc.new_asset("Good", "Iron")
    doc.set_text("english", "-1001", "A tiny hut")
    path = doc.save(tmp_path / "edits.json")
    again = ChangeSet.load(path)
    assert again == doc.changes and not doc.dirty


def test_the_modops_mod_merges_replaces_adds_and_removes(doc, game, tmp_path):
    doc.set_value(2001, "Building/Upkeep", "7")
    doc.set_block(2001, "Building/BuildCost", ET.fromstring("<BuildCost />"))
    doc.reset_value(2002, "Building/Upkeep")
    new = doc.new_asset("", "Wood 2", copy_of=1001)
    doc.remove_asset(1002)
    doc.set_text("english", "-1001", "A tiny hut")
    doc.set_text("english", "77", "A new line")
    files = modops.render(doc)
    root = ET.fromstring(files["data/base/config/export/assets.xml"])
    ops = [(op.get("Type"), op.get("GUID"), op.get("Path")) for op in root.iter("ModOp")]
    assert ("addNextSibling", "1001", None) in ops
    assert ("merge", "2001", "/Values") in ops and ("replace", "2001", "/Values/Building/BuildCost") in ops
    assert ("remove", "2002", "/Values/Building/Upkeep") in ops
    assert ("remove", None, "//Asset[Values/Standard/GUID='1002']") in ops
    merge = next(op for op in root.iter("ModOp") if op.get("Type") == "merge")
    assert merge.findtext("Building/Upkeep") == "7"
    added = next(op for op in root.iter("ModOp") if op.get("Type") == "addNextSibling")
    assert added.findtext("Asset/Values/Standard/GUID") == str(new)
    texts = ET.fromstring(files["data/base/config/gui/texts_english.xml"])
    assert [op.get("Type") for op in texts] == ["replace", "add"]  # the game's text replaced, the new one added
    assert texts[0].get("AllowNoMatch") is None and texts[1].findtext("Text/LineId") == "77"
    doc.game_texts = None  # without the game's texts: each text replaced if there, added if not
    texts = ET.fromstring(modops.render(doc)["data/base/config/gui/texts_english.xml"])
    assert [op.get("Type") for op in texts] == ["replace", "add", "replace", "add"]
    folder, problems = modops.write_mod(doc, tmp_path / "mods", game=game.files)
    assert problems == [] and (folder / "modinfo.json").is_file()


def test_building_the_mod_never_replaces_another_mods_folder(doc, tmp_path):
    doc.set_value(2001, "Building/Upkeep", "7")
    mods = tmp_path / "mods"
    other = mods / doc.changes.mod_id
    other.mkdir(parents=True)
    (other / "modinfo.json").write_text('{"ModID": "someone-else"}', encoding="utf-8")
    (other / "theirs.xml").write_text("<ModOps />", encoding="utf-8")
    with pytest.raises(ValueError, match="another mod"):
        modops.write_mod(doc, mods)
    assert (other / "theirs.xml").is_file()  # left as it was
    doc.changes.mod_id = ".."  # the mods folder's parent, not a mod folder
    with pytest.raises(ValueError, match="not a folder name"):
        modops.write_mod(doc, mods)
    assert (other / "theirs.xml").is_file()


def test_building_the_mod_again_replaces_its_own_folder(doc, tmp_path):
    doc.set_value(2001, "Building/Upkeep", "7")
    folder, _ = modops.write_mod(doc, tmp_path / "mods")
    (folder / "stale.xml").write_text("<ModOps />", encoding="utf-8")
    trashed: list = []
    again, _ = modops.write_mod(doc, tmp_path / "mods", trash=trashed.append)
    assert (
        len(trashed) == 1
        and again == folder
        and not (folder / "stale.xml").exists()
        and (folder / "modinfo.json").is_file()
    )


def test_the_native_files_carry_the_edits_and_leave_the_rest_as_shipped(doc, game):
    doc.set_value(2001, "Building/Upkeep", "7")
    new = doc.new_asset("", "Wood 2", copy_of=1001)
    loose = doc.new_asset("Mission", "Lost")
    loose_anchor = doc.changes.new[loose].anchor
    doc.changes.new[loose].anchor = None
    doc.remove_asset(1002)
    files = native.render(doc)
    data = files["data/base/config/export/assets.xml"]
    shipped = game.index.local[0].read_bytes()
    assert data[: game.index.by_guid[1001].start] == shipped[: game.index.by_guid[1001].start]
    root = ET.fromstring(data.decode("utf-8").lstrip("﻿"))
    guids = [
        a.findtext("Values/Standard/GUID") for a in root.iter("Asset") if a.find("Values/Standard/Name") is not None
    ]
    assert guids == ["1001", str(new), "2001", "2002", "3001", str(loose)] and loose_anchor == 3001
    hut = next(a for a in root.iter("Asset") if a.findtext("Values/Standard/GUID") == "2001")
    assert hut.findtext("Values/Building/Upkeep") == "7"


def test_the_check_finds_what_the_game_would_drop_or_refuse(doc):
    doc.set_value(2001, "Building/Level", "Medium")
    doc.set_value(2001, "Building/Kinds", "GameObject;Ghost")
    doc.set_value(2001, "Building/Upkeep", "-1")
    doc.set_value(2001, "Building/Colour", "red")
    doc.set_block(2001, "Building/BuildCost", ET.fromstring(
        "<BuildCost><Item><Product>2002</Product><Amount>400</Amount></Item><Item><Product>77</Product></Item></BuildCost>"
    ))  # fmt: skip
    doc.remove_asset(1002)
    found = [str(f) for f in checks.final_check(doc)]
    assert any("'Medium' is not in the data set Level" in f for f in found)
    assert any("Ghost not in the data set ObjectType" in f for f in found)
    assert any("below the minimum" in f for f in found) and any("above the maximum 100" in f for f in found)
    assert any("Colour" in f and "does not know" in f for f in found)
    assert any("no asset 77" in f for f in found) and any("has no Product" in f for f in found)
    assert any("1002" in f and "still name it" in f for f in found)


def test_a_new_guid_another_mod_uses_is_a_clash(doc, tmp_path):
    guid = doc.new_asset("Good", "Iron")
    other = tmp_path / "mods" / "other" / "data"
    other.mkdir(parents=True)
    (other / "assets.xml").write_text(f"<ModOps><Asset><Values><Standard><GUID>{guid}</GUID></Standard></Values>"
                                      "</Asset></ModOps>", encoding="utf-8")  # fmt: skip
    assert [f.guid for f in checks.clashes(doc, tmp_path / "mods")] == [guid]


def test_diff_summary_and_usage(doc):
    doc.set_value(2001, "Building/Upkeep", "7")
    new = doc.new_asset("", "Wood 2", copy_of=1001)
    doc.set_value(new, "Product/Price", "1002")
    assert [(d.path, d.before, d.after) for d in compare.diff_asset(doc, 2001)] == [("Building/Upkeep", None, "7")]
    lines = compare.summary(doc)
    assert lines[0].startswith("~ 2001 Hut: 1 values") and lines[1].startswith(f"+ {new} Wood 2: new (copy of 1001)")
    assert compare.usage(doc, 1002) == [3001, new]


def test_the_balancing_table_and_the_texts_go_out_and_back(doc, game):
    cols = ["Building/Upkeep", "Building/Level"]
    text = balancing.export_csv(doc, [2001, 2002], cols)
    assert text.splitlines()[1:] == ["2001,Hut,5,Low", "2002,Big Hut,9,Low"]
    assert balancing.grid(doc, [2001], cols)[0][2][0].origin == DEFAULT
    problems = balancing.import_csv(doc, text.replace("2001,Hut,5,Low", "2001,Hut,6,") + "77,Nobody,1,\n")
    assert problems == ["row 4: no asset 77"] and doc.changes.assets[2001].set == {"Building/Upkeep": "6"}
    texts = game.texts("english")
    assert texts == {"-1001": "A small hut"}
    out = balancing.export_texts(texts).replace("A small hut", "A wooden hut")
    assert balancing.import_texts(doc, "english", out, texts) == 1 and balancing.words(texts) == 3


def test_a_damaged_index_cache_is_scanned_again_not_a_crash(game, tmp_path):
    cached = next((tmp_path / "cache" / "asset-index").glob("*.json.gz"))
    for damaged in (b"\x1f\x8b\x08\x00garbage that is no gzip stream", b"", b"plain text"):
        cached.write_bytes(damaged)
        again = open_game("anno117", tmp_path / "117", tmp_path / "cache").index
        assert sorted(again.by_guid) == sorted(game.index.by_guid)


def test_a_search_for_a_digit_that_is_no_number_finds_nothing(game):
    assert game.index.search("\u00b2") == []  # a superscript two "is a digit" but no GUID


def test_an_empty_asset_file_has_no_assets(tmp_path):
    root = asset_game(tmp_path / "117")
    (root / "data/base/config/export/assets.xml").write_bytes(b"")
    assert open_game("anno117", root, tmp_path / "cache").index.entries == []


# The checks beyond references and numbers


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
