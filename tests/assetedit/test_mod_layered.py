"""Editing on top of an imported mod, layered (assetedit/layered.py, docs/mod-import.md "Phase 6 as built"): the base is
the game with the mod applied, the edits are an appended include, the mod's own files stay byte-identical, edits of a
mod's own assets are operations on top of the mod, and it coexists with the net change set of phase 2."""

from __future__ import annotations

import os

import pytest
from tests.sources.sourcesfakes import asset_game, fake_mod

from annotoolbox.assetedit import handover, layered, modimport
from annotoolbox.assetedit import projectpart as assets_part
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.changeset import CChangeSet
from annotoolbox.assetedit.origins import GAME, MOD_CHANGED, MOD_NEW, PERSON
from annotoolbox.suite import carriedpart, importmeta, projectimport
from annotoolbox.suite import project as projects
from annotoolbox.suite.settings import CSettings
from annotoolbox.textedit import projectpart as texts_part

ASSETS = "data/base/config/export/assets.xml"
TEXTS = "data/base/config/gui/texts_english.xml"
INCLUDE = "data/base/config/export/wood-mod-edits.include.xml"
NEW_ASSET = (
    "<Asset><Template>Good</Template><Values><Standard><GUID>100500</GUID><Name>Mod Wood</Name></Standard>"
    "<Product><Price>7</Price></Product></Values></Asset>"
)
OPS = f"""<ModOps>
<!-- the price of wood -->
<ModOp Type="merge" GUID="1001" Path="/Values/Product"><Price>20</Price></ModOp>
<ModOp Type="addNextSibling" GUID="1001">{NEW_ASSET}</ModOp>
</ModOps>
"""
TEXT_OPS = """<ModOps>
<ModOp Type="add" Path="/TextExport/Texts"><Text><LineId>5</LineId><Text>New line</Text></Text></ModOp>
</ModOps>
"""
OTHER = {"data/base/config/export/templates.xml": "<ModOps/>", "data/graphics/x.rdm": bytes(range(200))}


@pytest.fixture
def game_dir(tmp_path):
    return asset_game(tmp_path / "117")


@pytest.fixture
def settings(tmp_path, game_dir):
    return CSettings(anno117=str(game_dir), workspace=str(tmp_path / "ws"))


@pytest.fixture
def game(tmp_path, game_dir):
    return open_game("anno117", game_dir, tmp_path / "cache")


def imported(tmp_path, files=None, **info):
    mod = fake_mod(tmp_path / "mods", "wood-mod", {ASSETS: OPS, TEXTS: TEXT_OPS, **OTHER, **(files or {})}, **info)
    projectimport.import_mod(mod, tmp_path / "proj")
    return mod, projects.CProject.open(tmp_path / "proj")


def build(proj, settings):
    parts = [carriedpart.PART, assets_part.PART, texts_part.PART]
    return projects.build(proj, parts, settings, lambda _t: None)


def layer_on(proj, game):
    layered.take(proj, layered.plan(proj, game))


def edit(proj, game, change):
    """Save a change set made against the layered base."""
    base = layered.open_layered(proj, game)
    changes = CChangeSet(proj.game)
    change(changes, base)
    changes.save(assets_part.change_set_path(proj))
    return base


def test_the_plan_reads_the_layer_and_changes_nothing(tmp_path, game):
    _mod, proj = imported(tmp_path)
    before = carriedpart.files_of(carriedpart.carried_root(proj))
    plan = layered.plan(proj, game)
    assert plan.patch == ASSETS and plan.include == INCLUDE
    assert plan.layer.added == {100500} and plan.layer.changed == {1001} and not plan.layer.removed
    assert plan.kept == ["data/base/config/export/templates.xml"]
    text = "\n".join(plan.lines())
    assert "1 assets added, 1 changed and 0 removed" in text and INCLUDE in text and "templates.xml" in text
    assert carriedpart.files_of(carriedpart.carried_root(proj)) == before
    assert layered.state(proj) is None and not (proj.folder / "layered.json").exists()


def test_the_base_is_the_game_with_the_mod_and_marks_where_each_asset_comes_from(tmp_path, game):
    _mod, proj = imported(tmp_path)
    layer_on(proj, game)
    base = layered.open_layered(proj, game)
    assert (
        base.m_layer is not None
        and base.m_index.m_by_guid[100500].name == "Mod Wood"
        and 100500 not in game.m_index.m_by_guid
    )
    doc = base.document(CChangeSet("anno117"))
    assert (doc.origin(100500), doc.origin(1001), doc.origin(1002)) == (MOD_NEW, MOD_CHANGED, GAME)
    assert doc.asset(1001).findtext("Values/Product/Price") == "20"  # the mod's value, not the game's 12
    assert game.document(CChangeSet("anno117")).asset(1001).findtext("Values/Product/Price") == "12"
    assert doc.exists(100500) and doc.layers(100500).property_names()
    changes = CChangeSet("anno117")
    changes.m_new[7000] = modimport.SNewAsset("Good", "Mine")
    assert base.document(changes).origin(7000) == PERSON
    assert base.read_base(ASSETS) == base.m_files.read(ASSETS)
    untouched = game.m_index.raw(1002)
    assert base.m_index.raw(1002) == untouched  # an asset nobody read is copied from its own bytes


def test_without_edits_the_build_is_byte_identical_to_the_mod(tmp_path, game, settings):
    mod, proj = imported(tmp_path)
    layer_on(proj, game)
    build(proj, settings)
    diff = carriedpart.compare_trees(mod, proj.build_dir)
    assert diff.identical, diff.lines()
    assert (proj.build_dir / INCLUDE).exists() is False


def test_an_edit_is_an_include_on_top_and_the_mods_file_keeps_its_bytes(tmp_path, game, settings):
    mod, proj = imported(tmp_path)
    layer_on(proj, game)
    edit(proj, game, lambda c, base: c.change(1001).set.update({"Product/Price": "30"}))
    build(proj, settings)
    out = proj.build_dir
    original, built = (mod / ASSETS).read_bytes(), (out / ASSETS).read_bytes()
    head = original[: original.rindex(b"</ModOps>")]
    assert built.startswith(head) and b'<Include File="wood-mod-edits.include.xml"/>' in built[len(head) :]
    assert "<Price>30</Price>" in (out / INCLUDE).read_text(encoding="utf-8")
    diff = carriedpart.compare_trees(mod, out)
    assert diff.different == [ASSETS] and diff.extra == [INCLUDE] and not diff.missing
    # the loader's reading of the built mod: the game, the mod's operations, then the edit
    result = modimport.import_folder(game, out, search=[])
    assert result.changes.m_assets[1001].set["Product/Price"] == "30" and 100500 in result.changes.m_new


def test_an_edit_of_the_mods_own_asset_patches_the_mod_layer_not_the_game(tmp_path, game, settings):
    mod, proj = imported(tmp_path)
    layer_on(proj, game)

    def make(changes, base):
        changes.change(100500).set["Product/Price"] = "9"
        assert not base.document(changes).m_changes.m_new  # the mod's asset is not the change set's new asset

    edit(proj, game, make)
    build(proj, settings)
    text = (proj.build_dir / INCLUDE).read_text(encoding="utf-8")
    assert 'GUID="100500"' in text and "<Price>9</Price>" in text and "<Asset>" not in text
    assert (proj.build_dir / ASSETS).read_bytes().count(NEW_ASSET.encode()) == 1  # the mod's own add, untouched
    result = modimport.import_folder(game, proj.build_dir, search=[])
    assert 100500 in result.changes.m_new and "<Price>9</Price>" in result.changes.m_new[100500].xml


def test_a_new_asset_and_a_removal_of_a_mod_asset_are_written_on_top(tmp_path, game, settings):
    _mod, proj = imported(tmp_path)
    layer_on(proj, game)

    def make(changes, base):
        changes.m_new[base.document(changes).m_changes.next_guid(base.m_index.m_by_guid, (100501, 100600))] = (
            modimport.SNewAsset("Good", "Mine", None, None, 100500)
        )
        changes.m_removed.append(100500)

    edit(proj, game, make)
    build(proj, settings)
    text = (proj.build_dir / INCLUDE).read_text(encoding="utf-8")
    assert "<GUID>100501<" in text and ">Mine<" in text and "GUID='100500'" in text


def test_the_first_free_guid_skips_what_the_mod_added(tmp_path, game):
    _mod, proj = imported(tmp_path)
    layer_on(proj, game)
    base = layered.open_layered(proj, game)
    assert CChangeSet("anno117").next_guid(base.m_index.m_by_guid, (100500, 100600)) == 100501


def test_the_layer_is_cached_by_the_carried_files_and_made_again_when_they_change(tmp_path, game, monkeypatch):
    _mod, proj = imported(tmp_path)
    layer_on(proj, game)
    layered.open_layered(proj, game)

    def boom(*_a, **_k):
        raise AssertionError("applied again")

    monkeypatch.setattr(handover, "read_carried", boom)
    assert layered.open_layered(proj, game).m_layer.added == {100500}
    monkeypatch.undo()
    path = carriedpart.carried_root(proj) / ASSETS
    path.write_bytes(path.read_bytes().replace(b"<Price>20<", b"<Price>25<"))
    assert (
        layered.open_layered(proj, game).document(CChangeSet("anno117")).asset(1001).findtext("Values/Product/Price")
        == "25"
    )


def test_the_undo_names_what_it_drops_and_the_build_is_identical_again(tmp_path, game, settings):
    mod, proj = imported(tmp_path)
    layer_on(proj, game)
    edit(proj, game, lambda c, base: c.change(1001).set.update({"Product/Price": "30"}))
    with pytest.raises(layered.CLayerRefused, match="drops them"):
        layered.undo(proj)
    assert layered.state(proj) is not None and layered.has_edits(proj)
    assert layered.undo(proj, discard=True) and layered.state(proj) is None
    assert not assets_part.change_set_path(proj).exists()
    build(proj, settings)
    assert carriedpart.compare_trees(mod, proj.build_dir).identical
    layered.take(proj, layered.plan(proj, game))  # and it can be taken again


def test_layering_and_the_hand_over_exclude_each_other(tmp_path, game):
    _mod, proj = imported(tmp_path)
    assert layered.can_take(proj) and handover.can_take(proj)
    layer_on(proj, game)
    assert not layered.can_take(proj) and not handover.can_take(proj)
    with pytest.raises(handover.CHandoverRefused, match="layered"):
        handover.plan(proj, game)
    with pytest.raises(layered.CLayerRefused, match="already"):
        layered.plan(proj, game)
    layered.undo(proj)
    handover.take(proj, handover.plan(proj, game))
    assert not layered.can_take(proj)
    with pytest.raises(layered.CLayerRefused, match="hand-over"):
        layered.plan(proj, game)


def test_edits_already_in_the_change_set_refuse_layering(tmp_path, game):
    _mod, proj = imported(tmp_path)
    path = assets_part.change_set_path(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    held = CChangeSet("anno117")
    held.change(1001).set["Product/Price"] = "5"
    held.save(path)
    with pytest.raises(layered.CLayerRefused, match="holds work"):
        layered.plan(proj, game)


def test_a_mod_with_conditions_on_other_mods_stays_carried(tmp_path, game):
    ops = OPS.replace('<ModOp Type="merge" GUID="1001"', '<ModOp Type="merge" Condition="#other-mod" GUID="1001"', 1)
    _mod, proj = imported(tmp_path, {ASSETS: ops})
    with pytest.raises(layered.CLayerRefused, match="other-mod"):
        layered.plan(proj, game)
    assert layered.state(proj) is None


def test_a_patch_file_without_a_closing_tag_or_without_the_asset_patch_is_refused(tmp_path, game):
    _mod, proj = imported(tmp_path, {ASSETS: OPS.replace("</ModOps>", "")})
    with pytest.raises(layered.CLayerRefused, match="closing ModOps"):
        layered.plan(proj, game)
    only = fake_mod(tmp_path / "m2", "only-models", {"data/graphics/x.rdm": b"rdm"})
    projectimport.import_mod(only, tmp_path / "proj2")
    with pytest.raises(layered.CLayerRefused, match="no patch"):
        layered.plan(projects.CProject.open(tmp_path / "proj2"), game)


def test_an_empty_modops_root_and_the_include_name_that_is_free(tmp_path, game, settings):
    files = {ASSETS: "<ModOps/>\n", INCLUDE: "<ModOps/>\n"}
    mod, proj = imported(tmp_path, files)
    plan = layered.plan(proj, game)
    assert plan.include == "data/base/config/export/wood-mod-edits-2.include.xml"
    layered.take(proj, plan)
    edit(proj, game, lambda c, base: c.change(1001).set.update({"Product/Price": "30"}))
    build(proj, settings)
    built = (proj.build_dir / ASSETS).read_text(encoding="utf-8")
    assert built.startswith("<ModOps>") and "wood-mod-edits-2.include.xml" in built
    assert (mod / ASSETS).read_text(encoding="utf-8") == "<ModOps/>\n"


def test_the_texts_part_and_the_layered_assets_part_write_one_file_each(tmp_path, game, settings):
    mod, proj = imported(tmp_path)
    layer_on(proj, game)
    edit(proj, game, lambda c, base: c.change(1001).set.update({"Product/Price": "30"}))
    build(proj, settings)
    assert (proj.build_dir / TEXTS).read_bytes() == (mod / TEXTS).read_bytes()  # no texts edited: the mod's file
    assert importmeta.read_meta(proj.folder) is not None


def test_a_reimport_is_refused_while_layered(tmp_path, game):
    from annotoolbox.suite import reimport

    mod, proj = imported(tmp_path)
    layer_on(proj, game)
    with pytest.raises(projectimport.CImportRefused):
        reimport.plan(proj.folder, mod)


def test_the_command_line_reports_and_takes_and_undoes(tmp_path, game_dir, settings, capsys):
    import argparse

    from annotoolbox.assetedit import handovercommands

    _mod, proj = imported(tmp_path)
    settings_path = tmp_path / "settings.json"
    settings.save(settings_path)
    args = argparse.Namespace(folder=proj.folder, yes=False, undo=False, layered=True, settings=settings_path)
    assert handovercommands.cmd_handover(args) == 0 and layered.state(proj) is None
    assert "Layered editing" in capsys.readouterr().out
    args.yes = True
    assert handovercommands.cmd_handover(args) == 0 and layered.state(proj) is not None
    args.undo = True
    assert handovercommands.cmd_handover(args) == 0 and layered.state(proj) is None


def test_the_window_offers_both_ways_and_the_tree_marks_the_mods_assets(tmp_path, game, settings):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.assetedit.assettree import CAssetTree
    from annotoolbox.suite import apps, shell
    from annotoolbox.suite.projectpanel import CProjectDialog

    app = QApplication.instance() or QApplication([])
    _mod, proj = imported(tmp_path)
    settings.project = str(proj.folder)
    dialog = CProjectDialog(shell.CSuiteWindow([], settings, tmp_path / "settings.json"), apps.parts())
    take, undo = dialog.m_buttons["Edit on top of the mod (Assets)…"], dialog.m_buttons["Undo the layered editing"]
    hand = dialog.m_buttons["Hand the patch files to the Assets app…"]
    assert take.isEnabled() and hand.isEnabled() and not undo.isEnabled()
    dialog.take_handover("assets+1", is_waiting=True, confirm=lambda _p, _lines: False)
    assert "Nothing was handed over" in dialog.m_log.toPlainText() and layered.state(proj) is None
    dialog.take_handover("assets+1", is_waiting=True, confirm=lambda _p, lines: bool(lines))
    app.processEvents()
    assert "Layered editing is on" in dialog.m_log.toPlainText() and layered.state(proj) is not None
    assert not take.isEnabled() and not hand.isEnabled() and undo.isEnabled()
    base = layered.open_layered(proj, game)
    tree = CAssetTree()
    tree.set_doc(base.document(CChangeSet("anno117")))
    rows = {tree._row(g, icon=False).text(0) for g in (100500, 1001, 1002)}
    assert "Mod Wood [mod: new]" in rows and "Wood [mod: changed]" in rows and "Stone" in rows
    dialog.undo_handover("assets+1", confirm=lambda _p, _what: True)
    assert "Layered editing undone" in dialog.m_log.toPlainText() and take.isEnabled() and not undo.isEnabled()
