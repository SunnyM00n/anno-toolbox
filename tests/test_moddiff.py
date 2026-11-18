"""The Mod diff app's core (``moddiff``): a synthetic mod against the synthetic game, the steps of several mods and their
conflicts (value, block, removed, duplicate, file, overlap, who wins and what decided it), two versions of a mod, an
archive and a folder of loose files, the exports; and an installed game behind a skip (``ANNO117_DIR``)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.fakes import INSTALL_VARS, asset_game, fake_mod

from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.formats.rda import write_v22
from annotoolbox.moddiff import compare, effect, export, model
from annotoolbox.moddiff.base import GameBase
from annotoolbox.moddiff.model import ADDED, CHANGED, REMOVED
from annotoolbox.modloader.mods import Variables, read_mod

ASSETS = "data/base/config/export/assets.xml"
TEXTS = "data/base/config/gui/texts_english.xml"
TEMPLATES = "data/base/config/export/templates.xml"
NEW = "<Asset><Template>Good</Template><Values><Standard><GUID>{g}</GUID><Name>{n}</Name></Standard></Values></Asset>"


def ops(*lines: str) -> str:
    return "<ModOps>\n" + "\n".join(lines) + "\n</ModOps>"


def price(guid: int, value: int) -> str:
    return f'<ModOp Type="merge" GUID="{guid}" Path="/Values/Product"><Price>{value}</Price></ModOp>'


@pytest.fixture
def game(tmp_path):
    return open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache")


def mod_of(root: Path, mod_id: str, files: dict, **info) -> object:
    mod, why = read_mod(fake_mod(root, mod_id, files, **info), 8)
    assert mod is not None, why
    return mod


def target(report: model.Report, key: str, file: str = ASSETS) -> model.Target:
    return next(t for t in report.targets if t.ident == (file, key))


def test_a_mod_against_the_game_lists_assets_texts_data_and_files(game, tmp_path):
    mod = mod_of(tmp_path / "mods", "every", {
        ASSETS: ops(
            price(1001, 20),
            '<ModOp GUID="2001" Merge="Building/BuildCost"><ModItem Merge="Product"><Product>1002</Product>'
            "<Amount>1</Amount></ModItem></ModOp>",
            '<ModOp GUID="2002" Remove="Building"/>',
            f'<ModOp Type="addNextSibling" GUID="1002">{NEW.format(g=1003, n="Iron")}</ModOp>',
            '<ModOp Type="remove" Path="//Asset[Values/Standard/GUID=\'3001\']"/>',
            '<ModOp GUID="404" Merge="Standard"><Name>nobody</Name></ModOp>',
        ),
        TEXTS: ops('<ModOp Type="replace" Path="/TextExport/Texts/Text[LineId=\'-1001\']/Text"><Text>A big hut</Text></ModOp>',
                   '<ModOp Type="add" Path="/TextExport/Texts"><Text><LineId>5</LineId><Text>New</Text></Text></ModOp>'),
        TEMPLATES: ops('<ModOp Type="merge" Path="/Templates/Group/Template[Name=\'Good\']/Properties/Product">'
                       "<Price>11</Price></ModOp>"),
        "data/graphics/icon.dds": b"stand-in",
        "data/base/config/export/properties.xml.bak": "x",
    })  # fmt: skip
    report = compare.mod_against_game(game, mod)
    wood = target(report, "1001")
    assert wood.kind == CHANGED and wood.name == "Wood" and wood.template == "Good"
    assert [(c.path, c.before, c.after) for c in wood.changes] == [("Values/Product/Price", "12", "20")]
    assert wood.mods == ["every"]
    hut = target(report, "2001")
    assert [c.path for c in hut.changes] == ["Values/Building/BuildCost/Item[+]"]  # an item added to a list
    assert (
        target(report, "2002").changes[0].path.startswith("Values/Building") and target(report, "3001").kind == REMOVED
    )
    iron = target(report, "1003")
    assert iron.kind == ADDED and iron.name == "Iron" and iron.values()["Values"]["Standard"]["Name"] == "Iron"
    assert [(c.before, c.after) for c in target(report, "-1001", TEXTS).changes] == [("A small hut", "A big hut")]
    assert target(report, "5", TEXTS).kind == ADDED
    data = target(report, "", TEMPLATES)
    assert [(c.before, c.after) for c in data.changes] == [("10", "11")]
    assert [(f.path, f.kind) for f in report.files] == [
        ("data/base/config/export/properties.xml.bak", model.NEW),
        ("data/graphics/icon.dds", model.NEW),
    ]
    assert any("GUID 404" in line for line in report.dropped)  # what the loader would drop is no change
    assert not any(t.key == "404" for t in report.targets)
    assert report.counts()["added"] >= 2 and report.counts()["dropped"] >= 1


def test_an_operation_that_changes_nothing_is_no_change(game, tmp_path):
    mod = mod_of(tmp_path / "mods", "same", {ASSETS: ops(price(1001, 12))})
    assert compare.mod_against_game(game, mod).targets == []


def test_the_steps_of_mods_and_their_conflicts(game, tmp_path):
    root = tmp_path / "mods"
    a = mod_of(root, "a-mod", {ASSETS: ops(price(1001, 20), '<ModOp GUID="2002" Merge="Building"><Upkeep>7</Upkeep></ModOp>',
                                           f'<ModOp Type="addNextSibling" GUID="1002">{NEW.format(g=1500, n="Dup")}</ModOp>'),
                               "data/x/shared.dds": b"aaaa"})  # fmt: skip
    b = mod_of(root, "b-mod", {ASSETS: ops(price(1001, 30),
                                           f'<ModOp Type="addNextSibling" GUID="1002">{NEW.format(g=1500, n="Dup")}</ModOp>',
                                           '<ModOp GUID="2001" Merge="Standard"><Name>Renamed</Name></ModOp>'),
                               "data/x/shared.dds": b"bb"}, Dependencies={"LoadAfter": ["a-mod"]})  # fmt: skip
    c = mod_of(root, "c-mod", {ASSETS: ops('<ModOp GUID="2001" Merge="Building"><Upkeep>3</Upkeep></ModOp>',
                                           "<ModOp Type=\"remove\" Path=\"//Asset[Values/Standard/GUID='2002']\"/>")})  # fmt: skip
    report = compare.profile_conflicts(game, [a, b, c], root, "test")
    kinds = {(x.kind, x.key, x.path): x for x in report.conflicts}
    value = kinds[(model.VALUE, "1001", "Values/Product/Price")]
    assert value.mods == ("a-mod", "b-mod") and value.values == ("20", "30") and value.winner == "b-mod"
    assert value.decided == "LoadAfter" and value.level == model.CONFLICT
    assert target(report, "1001").changes[0].after == "30"  # the effective result: the later mod's value
    assert target(report, "1001").mods == ["a-mod", "b-mod"]
    assert kinds[(model.DUPLICATE, "1500", "")].mods == ("a-mod", "b-mod")
    overlap = kinds[(model.ASSET, "2001", "")]  # different values of one asset: they merge
    assert overlap.level == model.OVERLAP and overlap.mods == ("b-mod", "c-mod") and overlap.decided == "name order"
    gone = kinds[(model.GONE, "2002", "")]
    assert gone.mods == ("a-mod", "c-mod") and gone.winner == "c-mod"
    shared = next(x for x in report.conflicts if x.kind == model.FILE)
    assert shared.file == "data/x/shared.dds" and shared.winner == "b-mod" and shared.values == ("4 bytes", "2 bytes")
    assert [f.size for f in report.files if f.path == "data/x/shared.dds"] == [2]  # the later file wins
    assert report.counts()["conflicts"] >= 4 and report.counts()["overlaps"] == 1


def test_a_block_removed_above_a_changed_value_is_a_conflict(game, tmp_path):
    root = tmp_path / "mods"
    a = mod_of(
        root, "a-block", {ASSETS: ops('<ModOp GUID="2001" Merge="Building/Workers/Low"><Count>5</Count></ModOp>')}
    )
    b = mod_of(root, "b-block", {ASSETS: ops('<ModOp GUID="2001" Remove="Building/Workers"/>')},
               Dependencies={"LoadAfter": ["a-block"]})  # fmt: skip
    report = compare.profile_conflicts(game, [a, b], root, "t")
    block = next(x for x in report.conflicts if x.kind == model.BLOCK)
    assert (block.path, block.mods, block.values) == ("Values/Building/Workers", ("a-block", "b-block"), ("5", None))
    assert block.winner == "b-block" and target(report, "2001").changes[0].path == "Values/Building/Workers"


def test_two_versions_of_a_mod_differ_in_their_results(game, tmp_path):
    v1 = mod_of(
        tmp_path / "v1", "ver", {ASSETS: ops(price(1001, 20), price(1002, 7)), "data/f.dds": b"one"}, Version="1.0.0"
    )
    v2 = mod_of(tmp_path / "v2", "ver", {ASSETS: ops(price(1001, 25), price(1002, 7),
                                                     f'<ModOp Type="addNextSibling" GUID="1002">{NEW.format(g=1600, n="New")}</ModOp>'),
                                         "data/f.dds": b"two", "data/g.dds": b"g"}, Version="1.1.0")  # fmt: skip
    report = compare.two_versions(game, v1, v2, search=[])
    assert (report.left, report.right) == ("ver 1.0.0", "ver 1.1.0")
    wood = target(report, "1001")
    assert wood.kind == model.DIFFERS and [(c.path, c.before, c.after) for c in wood.changes] == [
        ("Values/Product/Price", "20", "25")
    ]
    assert not any(t.key == "1002" for t in report.targets)  # both set the same value: no difference
    assert target(report, "1600").kind == model.ONLY_B
    assert {(f.path, f.kind) for f in report.files} == {("data/f.dds", model.DIFFERS), ("data/g.dds", model.ONLY_B)}
    same = compare.two_versions(game, v1, v1, search=[])
    assert same.targets == [] and same.files == []


def test_a_value_only_one_version_changes_is_compared_with_the_games(game, tmp_path):
    v1 = mod_of(tmp_path / "v1", "ver", {ASSETS: ops(price(1001, 20))})
    v2 = mod_of(
        tmp_path / "v2",
        "ver",
        {ASSETS: ops('<ModOp GUID="2002" Merge="Building"><Upkeep>4</Upkeep></ModOp>')},
        Version="2",
    )
    report = compare.two_versions(game, v1, v2, search=[])
    assert {t.key: t.kind for t in report.targets} == {"1001": model.ONLY_A, "2002": model.ONLY_B}


def test_two_mods_together_use_the_loaders_order(game, tmp_path):
    root = tmp_path / "mods"
    a = mod_of(root, "zz-first", {ASSETS: ops(price(1001, 1))}, Dependencies={"LoadAfter": ["aa-second"]})
    b = mod_of(root, "aa-second", {ASSETS: ops(price(1001, 2))})
    report = compare.two_mods(game, a, b, search=[])
    assert report.mods == ["aa-second 1.0.0", "zz-first 1.0.0"]  # LoadAfter beats the name order
    only = report.conflicts[0]
    assert only.winner == "zz-first" and only.decided == "LoadAfter" and target(report, "1001").changes[0].after == "1"


def test_an_archive_and_a_folder_of_loose_files_against_the_game(game, tmp_path):
    changed = asset_game(tmp_path / "other")
    text = (changed / ASSETS).read_text(encoding="utf-8").replace("<Price>12</Price>", "<Price>99</Price>")
    files = {
        ASSETS: text.encode(),
        TEXTS: b"<TextExport><Texts><Text><LineId>-1001</LineId><Text>Hut!</Text></Text></Texts></TextExport>",
        TEMPLATES: b"<Templates><Group><Name>Objects</Name></Group></Templates>",
        "data/new/thing.bin": b"1234",
        "data/graphics/icon.dds": b"stand-in",
    }
    archive = tmp_path / "mod.rda"
    archive.write_bytes(write_v22(files))
    report = compare.archive_against_game(game, archive)
    assert [(c.path, c.before, c.after) for c in target(report, "1001").changes] == [
        ("Values/Product/Price", "12", "99")
    ]
    assert [c.after for c in target(report, "-1001", TEXTS).changes] == ["Hut!"]
    assert target(report, "", TEMPLATES).changes  # the data file's values differ
    assert {f.path: f.kind for f in report.files} == {
        "data/new/thing.bin": model.NEW,
        "data/graphics/icon.dds": model.NEW,
    }
    loose = tmp_path / "loose"
    for path, data in files.items():
        (loose / path).parent.mkdir(parents=True, exist_ok=True)
        (loose / path).write_bytes(data)
    folder = compare.archive_against_game(game, loose)
    assert {t.ident for t in folder.targets} == {t.ident for t in report.targets}
    own = tmp_path / "own"  # a file that is the game's own is said so and is no change
    (own / TEMPLATES).parent.mkdir(parents=True)
    (own / TEMPLATES).write_bytes((asset_game(tmp_path / "g2") / TEMPLATES).read_bytes())
    assert compare.archive_against_game(game, own).targets == []


def test_the_exports_name_conflicts_changes_and_files(game, tmp_path):
    root = tmp_path / "mods"
    a = mod_of(root, "a-exp", {ASSETS: ops(price(1001, 20)), "data/q.dds": b"a"})
    b = mod_of(
        root, "b-exp", {ASSETS: ops(price(1001, 30)), "data/q.dds": b"bb"}, Dependencies={"LoadAfter": ["a-exp"]}
    )
    report = compare.profile_conflicts(game, [a, b], root, "prof")
    text = export.markdown(report, full=True)
    assert text.startswith("# profile prof") and "## Conflicts" in text and "Values/Product/Price" in text
    assert text.index("## Conflicts") < text.index("## Changes") and "a-exp" in text and "`data/q.dds`" in text
    assert "This is the summary" in export.markdown(report) and "This is the summary" not in text
    rows = export.csv_rows(report)
    assert rows[0][0] == "kind" and any(r[0] == "conflict" and "1001" in r for r in rows)
    assert any(r[0] == "value" and r[3] == "Values/Product/Price" for r in rows)
    assert any(r[0] == "file" and r[1] == "data/q.dds" for r in rows)
    out = tmp_path / "r.csv"
    export.write(report, out)
    assert out.read_text(encoding="utf-8-sig").splitlines()[0].startswith("kind,")
    export.write(report, tmp_path / "r.md")
    assert (tmp_path / "r.md").read_text(encoding="utf-8").startswith("# ")


def installed_folder(key: str) -> Path:
    folder = os.environ.get(INSTALL_VARS[key])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"needs an installed game ({INSTALL_VARS[key]})")
    return Path(folder)


@pytest.mark.parametrize("key", ["anno117", "anno1800"])
def test_a_synthetic_mod_against_an_installed_game(key, tmp_path):
    game = open_game(key, installed_folder(key), tmp_path / "cache")
    guid = next(e.guid for e in game.index.entries if e.file == game.index.files.index(game.layout.patch_target))
    probe = f'<ModOp GUID="{guid}" Type="add" Path="/Values/Standard"><ModDiffProbe>7</ModDiffProbe></ModOp>'  # the form both games read
    ghost = '<ModOp GUID="999999999" Type="merge" Path="/Values/Standard"><Name>nobody</Name></ModOp>'
    mod = mod_of(tmp_path / "mods", "live", {game.layout.patch_target: ops(probe, ghost)})
    report = compare.mod_against_game(game, mod)
    found = next(t for t in report.targets if t.key == str(guid))
    assert found.kind == CHANGED and [c.path for c in found.changes] == ["Values/Standard/ModDiffProbe"]
    assert len(report.targets) == 1 and any("999999999" in d for d in report.dropped)


@pytest.mark.parametrize("key", ["anno2070", "anno1404", "anno2205"])
def test_an_archive_with_a_new_file_against_an_installed_old_game(key, tmp_path):
    game = open_game(key, installed_folder(key), tmp_path / "cache")
    archive = tmp_path / "probe.rda"
    archive.write_bytes(write_v22({"data/moddiff/probe.bin": b"probe"}))
    report = compare.archive_against_game(game, archive)
    assert report.targets == [] and [(f.path, f.kind) for f in report.files] == [("data/moddiff/probe.bin", model.NEW)]


def test_a_mod_that_sets_a_value_an_earlier_mod_set_touches_it(game, tmp_path):
    root = tmp_path / "mods"
    a = mod_of(root, "a-set", {ASSETS: ops(price(1001, 20))})
    b = mod_of(root, "b-set", {ASSETS: ops(price(1001, 20))}, Dependencies={"LoadAfter": ["a-set"]})
    report = compare.profile_conflicts(game, [a, b], root, "t")
    same = next(c for c in report.conflicts if c.kind == model.VALUE)
    assert (same.level, same.mods, same.values) == (model.OVERLAP, ("a-set", "b-set"), ("20", "20"))  # nothing is lost
    assert target(report, "1001").mods == ["a-set", "b-set"] and report.counts()["conflicts"] == 0
    c = mod_of(root, "c-set", {ASSETS: ops(price(1001, 25))}, Dependencies={"LoadAfter": ["b-set"]})
    three = compare.profile_conflicts(game, [a, b, c], root, "t")
    clash = next(x for x in three.conflicts if x.kind == model.VALUE)
    assert (clash.level, clash.mods, clash.values, clash.winner) == (
        model.CONFLICT,
        ("a-set", "b-set", "c-set"),
        ("20", "20", "25"),
        "c-set",
    )
    alone = compare.profile_conflicts(game, [b], root, "t")  # a mod alone: no conflict, the value is its own
    assert alone.conflicts == [] and target(alone, "1001").mods == ["b-set"]


def amount(value: int) -> str:
    return f'<ModOp GUID="2001" Merge="Building/BuildCost"><Item><Amount>{value}</Amount></Item></ModOp>'


def keyed(value: int) -> str:
    item = f'<ModItem Merge="Product"><Product>1001</Product><Amount>{value}</Amount></ModItem>'
    return f'<ModOp GUID="2001" Merge="Building/BuildCost">{item}</ModOp>'


def calculated(expr: str) -> str:
    return f'<ModOp GUID="1001" Merge="Product"><Price><ModValue Insert="{expr}" /></Price></ModOp>'


def value_rows(report, path: str):
    return [c for c in report.conflicts if c.kind == model.VALUE and c.path == path]


@pytest.mark.parametrize("second", [amount, keyed], ids=["plain item", "ModItem by key"])
def test_a_value_set_inside_a_list_to_what_it_is_counts_as_touched(game, tmp_path, second):
    root = tmp_path / "mods"
    path = "Values/Building/BuildCost/Item[0]/Amount"
    a = mod_of(root, "a-list", {ASSETS: ops(amount(9))})
    b = mod_of(root, "b-list", {ASSETS: ops(second(9))}, Dependencies={"LoadAfter": ["a-list"]})
    report = compare.profile_conflicts(game, [a, b], root, "t")
    (row,) = value_rows(report, path)  # the entry is named by its place in the list
    assert (row.level, row.mods, row.values) == (model.OVERLAP, ("a-list", "b-list"), ("9", "9"))
    assert target(report, "2001").mods == ["a-list", "b-list"]
    c = mod_of(root, "c-list", {ASSETS: ops(second(7))}, Dependencies={"LoadAfter": ["b-list"]})
    clash = value_rows(compare.profile_conflicts(game, [a, b, c], root, "t"), path)
    assert [(x.level, x.values, x.winner) for x in clash] == [(model.CONFLICT, ("9", "9", "7"), "c-list")]


def test_a_value_a_modvalue_calculates_is_read_after_the_operation(game, tmp_path):
    root = tmp_path / "mods"
    a = mod_of(root, "a-calc", {ASSETS: ops(price(1001, 20))})
    b = mod_of(root, "b-calc", {ASSETS: ops(calculated("number(20)"))}, Dependencies={"LoadAfter": ["a-calc"]})
    report = compare.profile_conflicts(game, [a, b], root, "t")
    (row,) = value_rows(report, "Values/Product/Price")
    assert (row.level, row.values) == (model.OVERLAP, ("20", "20"))
    c = mod_of(root, "c-calc", {ASSETS: ops(calculated("self::node() + 5"))}, Dependencies={"LoadAfter": ["b-calc"]})
    (clash,) = value_rows(compare.profile_conflicts(game, [a, b, c], root, "t"), "Values/Product/Price")
    assert (clash.level, clash.values) == (model.CONFLICT, ("20", "20", "25"))


def test_a_list_entry_a_mod_appends_is_an_addition_not_a_set_value(game, tmp_path):
    root = tmp_path / "mods"
    new = '<ModOp GUID="2001" Merge="Building/BuildCost"><Item><Product>1002</Product><Amount>1</Amount></Item><Item><Product>1002</Product><Amount>2</Amount></Item></ModOp>'
    one = mod_of(root, "a-more", {ASSETS: ops(new)})
    report = compare.mod_against_game(game, one)
    paths = [c.path for c in target(report, "2001").changes]
    assert paths[-1] == "Values/Building/BuildCost/Item[+]" and "Values/Building/BuildCost/Item[0]/Amount" in paths
    run = effect.run_mods(game, [one], Variables([one], {}))
    assert set(run.steps[0].writes[(ASSETS, "2001")]) == {  # only the entry that was there is a set value
        "Values/Building/BuildCost/Item[0]/Product",
        "Values/Building/BuildCost/Item[0]/Amount",
    }


def test_an_asset_added_again_is_noted_and_compared_with_the_games(game, tmp_path):
    new = f'<ModOp Type="addNextSibling" GUID="1002">{NEW.format(g=1001, n="Twin")}</ModOp>'
    report = compare.mod_against_game(game, mod_of(tmp_path / "mods", "again", {ASSETS: ops(new)}))
    assert any("GUID 1001 is added again" in n for n in report.notes)
    twin = target(report, "1001")  # the later definition is what the file holds: its values against the game's
    assert twin.kind == CHANGED and ("Values/Standard/Name", "Wood", "Twin") in [
        (c.path, c.before, c.after) for c in twin.changes
    ]


def test_an_added_asset_keeps_its_values_in_a_few_bytes(game, tmp_path):
    new = f'<ModOp Type="addNextSibling" GUID="1002">{NEW.format(g=1700, n="Brick")}</ModOp>'
    brick = target(compare.mod_against_game(game, mod_of(tmp_path / "mods", "brick", {ASSETS: ops(new)})), "1700")
    assert (
        brick.kind == ADDED and brick.values()["Values"]["Standard"]["Name"] == "Brick" and 0 < len(brick.packed) < 200
    )


def test_the_files_of_a_mod_come_with_their_sizes_from_the_listing(tmp_path):
    mod = mod_of(tmp_path / "mods", "sized", {"data/a/one.bin": b"12345", "data/two.bin": b"1", ASSETS: ops()})
    assert mod.size_of("data/a/one.bin") == 5 and mod.size_of("data/two.bin") == 1 and mod.size_of("missing") == 0
    assert mod.files() == sorted(mod.files()) and "modinfo.json" in mod.files()


def test_the_keyed_lookup_reads_a_plain_path_like_the_element_trees(tmp_path):
    from annotoolbox.modloader.patchdoc import PatchDoc, leaf_text

    doc = PatchDoc.load(
        b"<R><Assets><Asset><Values><Standard><GUID>7</GUID><GUID>8</GUID></Standard><Standard><GUID>9</GUID></Standard>"
        b"</Values></Asset></Assets></R>"
    )
    asset = doc.top.find(".//Asset")
    for key in ("Values/Standard/GUID", "Values/Standard", "Values", "Nothing/Here", "Values/*/GUID"):
        assert doc.key_values(asset, key) == [leaf_text(n) for n in asset.iterfind(key)]
    assert doc.key_values(asset, "Values/Standard/GUID") == ["7", "8", "9"]


def test_a_text_file_no_operation_reached_is_not_read_again(game, tmp_path):
    mod = mod_of(
        tmp_path / "mods",
        "texty",
        {
            TEXTS: ops(
                '<ModOp Type="replace" Path="/TextExport/Texts/Text[LineId=\'-999\']/Text"><Text>x</Text></ModOp>'
            )
        },
    )
    report = compare.mod_against_game(game, mod)
    assert report.targets == [] and any("No matching node" in d for d in report.dropped)


def test_a_keyed_table_is_kept_while_only_other_children_of_a_keyed_node_change():
    import xml.etree.ElementTree as ET

    from annotoolbox.modloader.patchdoc import PatchDoc

    doc = PatchDoc.load(
        b"<R><Texts><Text><LineId>1</LineId><Text>a</Text></Text><Text><LineId>2</LineId></Text></Texts></R>"
    )
    texts = doc.top.find(".//Texts")
    first = texts[0]
    assert doc.keyed(texts, "Text", "LineId")["1"] == [first]
    doc.insert(first, len(first), [ET.fromstring("<Extra>x</Extra>")])  # a child the key does not read
    assert ("Text", "LineId") in doc._keys[texts]
    doc.remove(first.find("Text"))
    assert ("Text", "LineId") in doc._keys[texts]
    doc.insert(first, 0, [ET.fromstring("<LineId>9</LineId>")])  # one it reads: the table is made again
    assert ("Text", "LineId") not in doc._keys.get(texts, {})
    assert doc.keyed(texts, "Text", "LineId")["9"] == [first]


def test_an_asset_only_one_version_adds_keeps_its_values_and_the_notes_of_both_runs_are_kept(game, tmp_path):
    new = f'<ModOp Type="addNextSibling" GUID="1002">{NEW.format(g=1600, n="New")}</ModOp>'
    again = f'<ModOp Type="addNextSibling" GUID="1002">{NEW.format(g=1001, n="Twin")}</ModOp>'
    v1 = mod_of(tmp_path / "v1", "ver", {ASSETS: ops(price(1001, 20))})
    v2 = mod_of(tmp_path / "v2", "ver", {ASSETS: ops(new, again)}, Version="2")
    report = compare.two_versions(game, v1, v2, search=[])
    assert (
        target(report, "1600").kind == model.ONLY_B
        and target(report, "1600").values()["Values"]["Standard"]["Name"] == "New"
    )
    assert any(n.startswith("ver 2:") and "GUID 1001 is added again" in n for n in report.notes)


def test_an_installed_mod_id_wins_over_a_folder_of_that_name_in_the_working_directory(game, tmp_path, monkeypatch):
    from annotoolbox.moddiff import jobs
    from annotoolbox.modmgr.manager import ModManager

    root = tmp_path / "mods"
    mod_of(root, "same-name", {ASSETS: ops(price(1001, 20))})
    manager = ModManager("anno117", root)
    manager.scan()
    elsewhere = tmp_path / "cwd"
    fake_mod(elsewhere, "same-name", {ASSETS: ops(price(1001, 99))})
    monkeypatch.chdir(elsewhere)
    assert jobs.find_mod("same-name", manager, "anno117").folder == (root / "same-name")
    assert jobs.find_mod(str(elsewhere / "same-name"), manager, "anno117").folder == elsewhere / "same-name"


def test_two_archives_are_told_apart_past_the_byte_for_byte_limit(game, tmp_path):
    from annotoolbox.moddiff import archivediff

    def run(content: bytes):
        archive = tmp_path / f"a{len(content)}{content[:1].decode()}.rda"
        archive.write_bytes(write_v22({"data/x/a.bin": content, "data/x/b.bin": b"same"}))
        step = archivediff.compare_members(game, archivediff.members_of(archive, game), archive.name, compare_bytes=0)
        return compare.Effect("anno117", [step], [], step.files)

    report = compare.versions_report(run(b"1111"), run(b"2222"), "one", "two")
    assert [(f.path, f.kind) for f in report.files] == [("data/x/a.bin", model.DIFFERS)]  # same size, other bytes


def test_a_file_two_mods_carry_with_one_content_is_an_overlap(game, tmp_path):
    root = tmp_path / "mods"
    a = mod_of(root, "a-same", {"data/x/lib.dds": b"shared", "data/x/own.dds": b"aa"})
    b = mod_of(
        root, "b-same", {"data/x/lib.dds": b"shared", "data/x/own.dds": b"bb"}, Dependencies={"LoadAfter": ["a-same"]}
    )
    report = compare.profile_conflicts(game, [a, b], root, "t")
    levels = {c.file: c.level for c in report.conflicts if c.kind == model.FILE}
    assert levels == {"data/x/lib.dds": model.OVERLAP, "data/x/own.dds": model.CONFLICT}


def test_a_data_file_two_mods_patch_is_compared_against_the_games_once_each(game, tmp_path):
    root = tmp_path / "mods"
    path = '<ModOp Type="merge" Path="/Templates/Group/Template[Name=\'Good\']/Properties/Product"><Price>{}</Price></ModOp>'
    a = mod_of(root, "a-data", {TEMPLATES: ops(path.format(11))})
    b = mod_of(
        root,
        "b-data",
        {TEMPLATES: ops(path.format(12)), ASSETS: ops(price(1001, 5))},
        Dependencies={"LoadAfter": ["a-data"]},
    )
    c = mod_of(root, "c-data", {TEXTS: ops()}, Dependencies={"LoadAfter": ["b-data"]})
    report = compare.profile_conflicts(game, [a, b, c], root, "t")
    data = target(report, "", TEMPLATES)
    assert [(x.before, x.after) for x in data.changes] == [("10", "12")] and data.mods == ["a-data", "b-data"]


def test_the_value_diff_ignores_a_reorder_pairs_the_rest_and_can_keep_long_values_whole():
    from annotoolbox.sources.valuediff import diff_data

    out: list = []
    diff_data({"Item": [{"a": "1"}, {"a": "2"}]}, {"Item": [{"a": "2"}, {"a": "1"}]}, "", out)
    assert out == []  # a reorder is no change
    diff_data({"Item": [{"a": "1"}, {"a": "2"}]}, {"Item": [{"a": "2"}, {"a": "3"}, {"a": "4"}]}, "", out)
    assert out == [("Item[1]/a", "1", "3"), ("Item[+]", None, '{"a":"4"}')]  # named by the new list's position
    long_a, long_b = {"v": "x" * 200 + "a"}, {"v": "x" * 200 + "b"}
    short, whole = [], []
    diff_data({}, {"Block": long_a}, "", short)
    diff_data({}, {"Block": long_b}, "", whole, full=True)
    assert short[0][2].endswith("...") and not whole[0][2].endswith("...") and whole[0][2].endswith('b"}')


def test_two_long_blocks_that_differ_late_are_a_difference_between_versions(game, tmp_path):
    def block(tail: str) -> str:
        return (
            f'<ModOp GUID="2001" Merge="Building"><Extra><Note>{"x" * 300}{tail}</Note><Other>1</Other></Extra></ModOp>'
        )

    v1 = mod_of(tmp_path / "v1", "ver", {ASSETS: ops(block("a"))})
    v2 = mod_of(tmp_path / "v2", "ver", {ASSETS: ops(block("b"))}, Version="2")
    report = compare.two_versions(game, v1, v2, search=[])
    assert [t.key for t in report.targets] == ["2001"] and 'xb"' in report.targets[0].changes[0].after


def test_a_file_no_later_mod_patches_is_let_go_when_its_mod_is_done(game, tmp_path):
    root = tmp_path / "mods"
    a = mod_of(
        root,
        "a-stream",
        {
            ASSETS: ops(price(1001, 20)),
            TEXTS: ops(
                '<ModOp Type="add" Path="/TextExport/Texts"><Text><LineId>6</LineId><Text>Timber</Text></Text></ModOp>'
            ),
        },
    )
    b = mod_of(root, "b-stream", {ASSETS: ops(price(1001, 30))}, Dependencies={"LoadAfter": ["a-stream"]})
    run = effect.Run(GameBase(game), lambda _text: None)
    variables = Variables([a, b], {})
    run.step(a, variables, 0, later={ASSETS})
    assert set(run.session.docs) == {ASSETS}  # the texts are done, the assets wait for b
    run.step(b, variables, 0, later=set())
    assert run.session.docs == {} and run.shots == {}
    nets = {ident: t.kind for ident, t in run.nets.items()}
    assert nets == {(ASSETS, "1001"): CHANGED, (TEXTS, "6"): ADDED}
    both = run.nets[(ASSETS, "1001")]
    assert [(c.path, c.after) for c in both.changes] == [("Values/Product/Price", "30")]


def test_two_mods_that_only_set_the_value_that_was_there_are_an_overlap_not_a_crash(game, tmp_path):
    root = tmp_path / "mods"
    a = mod_of(root, "a-same", {ASSETS: ops(price(1001, 12))})
    b = mod_of(root, "b-same", {ASSETS: ops(price(1001, 12))}, Dependencies={"LoadAfter": ["a-same"]})
    report = compare.profile_conflicts(game, [a, b], root, "t")
    assert report.targets == []  # nothing changed in the game's files
    (row,) = report.conflicts
    assert (row.kind, row.level, row.path, row.values) == (
        model.VALUE,
        model.OVERLAP,
        "Values/Product/Price",
        ("12", "12"),
    )


def test_a_text_both_versions_add_with_other_words_differs(game, tmp_path):
    def text(words: str) -> str:
        return ops(
            f'<ModOp Type="add" Path="/TextExport/Texts"><Text><LineId>777</LineId><Text>{words}</Text></Text></ModOp>'
        )

    v1 = mod_of(tmp_path / "v1", "ver", {TEXTS: text("one")})
    v2 = mod_of(tmp_path / "v2", "ver", {TEXTS: text("two")}, Version="2")
    report = compare.two_versions(game, v1, v2, search=[])
    found = target(report, "777", TEXTS)
    assert found.kind == model.DIFFERS and [(c.before, c.after) for c in found.changes] == [("one", "two")]
    assert compare.two_versions(game, v1, v1, search=[]).targets == []


def test_files_past_the_byte_for_byte_limit_are_read_again_to_tell_two_versions_apart(game, tmp_path):
    from annotoolbox.moddiff import filechanges

    v1 = mod_of(tmp_path / "v1", "ver", {"data/f.dds": b"one", "data/g.dds": b"same"})
    v2 = mod_of(tmp_path / "v2", "ver", {"data/f.dds": b"two", "data/g.dds": b"same"}, Version="2")
    base = GameBase(game)
    one, two = (filechanges.mod_files(base, m, compare_bytes=0) for m in (v1, v2))
    assert not any(f.digest for f in one) and filechanges.read_source(one[0].source) == b"one"
    assert [(f.path, f.kind) for f in compare.diff_files(one, two)] == [("data/f.dds", model.DIFFERS)]
