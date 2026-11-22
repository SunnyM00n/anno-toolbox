"""Handing an imported mod's patch files to the Assets and Texts apps (assetedit/handover.py, suite/handoverdialog.py):
the report of what is lost, the hand-over, the build that writes the ModOps back, the undo, and that a project whose
hand-over was not taken still round-trips byte-identical."""

from __future__ import annotations

import os

import pytest
from tests.sources.sourcesfakes import asset_game, fake_mod

from annotoolbox.assetedit import handover
from annotoolbox.assetedit import projectpart as assets_part
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.changeset import CChangeSet
from annotoolbox.suite import carriedpart, importmeta, projectimport
from annotoolbox.suite import project as projects
from annotoolbox.suite.settings import CSettings
from annotoolbox.textedit import projectpart as texts_part

ASSETS = "data/base/config/export/assets.xml"
INCLUDE = "data/base/config/export/extra.include.xml"
TEXTS = "data/base/config/gui/texts_english.xml"
OPS = """<ModOps>
<!-- the price of wood -->
<ModOp Type="merge" GUID="1001" Path="/Values/Product"><Price>20</Price></ModOp>
<Include File="extra.include.xml"/>
<ModOp Type="merge" GUID="1001" Path="/Values/Standard" Condition="#other-mod"><Name>Pine</Name></ModOp>
</ModOps>
"""
INCLUDED = '<ModOps><ModOp Type="merge" GUID="1001" Path="/Values/Standard"><Name>Oak</Name></ModOp></ModOps>\n'
TEXT_OPS = """<ModOps>
<ModOp Type="replace" Path="/TextExport/Texts/Text[LineId='-1001']/Text"><Text>A big hut</Text></ModOp>
<ModOp Type="add" Path="/TextExport/Texts"><Text><LineId>5</LineId><Text>New line</Text></Text></ModOp>
</ModOps>
"""
OTHER = {
    "data/base/config/export/templates.xml": "<ModOps/>",
    "data/graphics/x.rdm": bytes(range(200)),
    INCLUDE: INCLUDED,
}


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


def test_a_project_whose_hand_over_was_not_taken_round_trips_with_the_apps_parts_in_the_build(tmp_path, settings):
    mod, proj = imported(tmp_path)
    build(proj, settings)
    diff = carriedpart.compare_trees(mod, proj.build_dir)
    assert diff.identical, diff.lines()
    assert handover.can_take(proj) and handover.state(proj) is None


def test_the_plan_names_what_is_lost_and_changes_nothing(tmp_path, game):
    _mod, proj = imported(tmp_path)
    before = carriedpart.files_of(carriedpart.carried_root(proj))
    plan = handover.plan(proj, game)
    assert plan.taken == [ASSETS, TEXTS] and plan.includes == [INCLUDE]
    assert plan.kept == ["data/base/config/export/templates.xml"]
    text = "\n".join(plan.lines())
    assert "1 comments in " + ASSETS in text and "Condition attributes in " + ASSETS in text
    assert "include files " + INCLUDE in text and "templates.xml" in text and "#other-mod" in text
    assert "1 changed" in text and "2 texts" in text
    assert carriedpart.files_of(carriedpart.carried_root(proj)) == before
    assert not (proj.folder / handover.STAGE).exists() and not (proj.folder / handover.STORE).exists()
    assert not assets_part.change_set_path(proj).exists() and handover.state(proj) is None


def test_taking_it_moves_the_files_and_the_build_writes_modops_from_the_change_set(tmp_path, game, settings):
    mod, proj = imported(tmp_path)
    plan = handover.plan(proj, game)
    record = handover.take(proj, plan)
    root = carriedpart.carried_root(proj)
    assert record.files == [ASSETS, TEXTS, INCLUDE]
    assert not any((root / rel).exists() for rel in record.files)
    assert (root / "data/graphics/x.rdm").is_file() and (root / "data/base/config/export/templates.xml").is_file()
    stored = handover.store_root(proj)
    assert (stored / ASSETS).read_bytes() == (mod / ASSETS).read_bytes()
    changes = CChangeSet.load(assets_part.change_set_path(proj))
    assert changes.m_assets[1001].set == {"Product/Price": "20", "Standard/Name": "Oak"}
    assert changes.m_carry == [] and changes.m_mod_id == proj.mod_id and changes.m_texts["english"]["5"] == "New line"
    assert not handover.can_take(proj) and handover.state(proj).files == record.files
    build(proj, settings)
    out = proj.build_dir
    assert "<Price>20</Price>" in (out / ASSETS).read_text(encoding="utf-8")
    assert "New line" in (out / TEXTS).read_text(encoding="utf-8") and not (out / INCLUDE).exists()
    assert (out / "data/graphics/x.rdm").read_bytes() == bytes(range(200))
    diff = carriedpart.compare_trees(mod, out)
    assert sorted(diff.different) == sorted([ASSETS, TEXTS]) and diff.missing == [INCLUDE] and not diff.extra


def test_the_undo_brings_the_original_files_back_and_the_build_is_identical_again(tmp_path, game, settings):
    mod, proj = imported(tmp_path)
    handover.take(proj, handover.plan(proj, game))
    assert handover.undo(proj) == 3
    assert handover.state(proj) is None and not assets_part.change_set_path(proj).exists()
    assert not (proj.folder / handover.STORE).exists() and handover.can_take(proj)
    build(proj, settings)
    assert carriedpart.compare_trees(mod, proj.build_dir).identical
    handover.take(proj, handover.plan(proj, game))  # and it can be taken again


def test_an_edit_since_is_named_and_the_undo_needs_to_be_told_to_drop_it(tmp_path, game):
    _mod, proj = imported(tmp_path)
    handover.take(proj, handover.plan(proj, game))
    assert handover.edited_since(proj) == []
    path = assets_part.change_set_path(proj)
    changes = CChangeSet.load(path)
    changes.change(1001).set["Product/Price"] = "99"
    changes.save(path)
    assert handover.edited_since(proj) == ["asset changes"]
    with pytest.raises(handover.CHandoverRefused, match="asset changes"):
        handover.undo(proj)
    assert handover.state(proj) is not None and path.is_file()
    assert handover.undo(proj, discard=True) == 3 and not path.exists()


def test_a_change_set_that_was_empty_before_is_restored_by_the_undo(tmp_path, game):
    _mod, proj = imported(tmp_path)
    path = assets_part.change_set_path(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    CChangeSet("anno117", "wood-mod").save(path)
    before = path.read_bytes()
    handover.take(proj, handover.plan(proj, game))
    assert path.read_bytes() != before
    handover.undo(proj)
    assert path.read_bytes() == before


def test_the_refusals_say_why_and_change_nothing(tmp_path, game):
    _mod, proj = imported(tmp_path)
    path = assets_part.change_set_path(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    held = CChangeSet("anno117", "wood-mod")
    held.change(1001).set["Product/Price"] = "5"
    held.save(path)
    with pytest.raises(handover.CHandoverRefused, match="already"):
        handover.plan(proj, game)
    path.unlink()
    plan = handover.plan(proj, game)
    handover.take(proj, plan)
    with pytest.raises(handover.CHandoverRefused, match="undo that hand-over"):
        handover.plan(proj, game)
    with pytest.raises(handover.CHandoverRefused, match="undo that hand-over"):
        handover.take(proj, plan)
    handover.undo(proj)
    with pytest.raises(handover.CHandoverRefused, match="no hand-over"):
        handover.undo(proj)
    plain = projects.CProject(tmp_path / "plain", mod_id="plain", game="anno117")
    with pytest.raises(handover.CHandoverRefused, match="not made by importing"):
        handover.plan(plain, game)


def test_a_mod_with_no_patch_of_the_assets_or_texts_has_nothing_to_hand_over(tmp_path, game):
    mod = fake_mod(tmp_path / "mods", "only-models", {"data/graphics/x.rdm": b"rdm"})
    projectimport.import_mod(mod, tmp_path / "proj")
    proj = projects.CProject.open(tmp_path / "proj")
    with pytest.raises(handover.CHandoverRefused, match="nothing|no patch"):
        handover.plan(proj, game)


def test_an_include_that_another_patch_file_uses_too_stays_carried(tmp_path, game):
    files = {"data/base/config/export/templates.xml": '<ModOps><Include File="extra.include.xml"/></ModOps>'}
    _mod, proj = imported(tmp_path, files)
    plan = handover.plan(proj, game)
    assert plan.includes == [] and INCLUDE in "\n".join(plan.lost)
    record = handover.take(proj, plan)
    assert record.files == [ASSETS, TEXTS] and (carriedpart.carried_root(proj) / INCLUDE).is_file()


def test_a_failed_move_puts_everything_back(tmp_path, game, monkeypatch):
    _mod, proj = imported(tmp_path)
    plan = handover.plan(proj, game)
    real, calls = os.replace, []

    def flaky(src, dest):
        calls.append(src)
        if len(calls) == 3:
            raise OSError("disk")
        return real(src, dest)

    monkeypatch.setattr(handover.os, "replace", flaky)
    with pytest.raises(OSError):
        handover.take(proj, plan)
    monkeypatch.undo()
    root = carriedpart.carried_root(proj)
    assert all((root / rel).is_file() for rel in (ASSETS, TEXTS, INCLUDE))
    assert handover.state(proj) is None and not assets_part.change_set_path(proj).exists()


def test_the_command_line_and_the_window_take_and_undo(tmp_path, game, settings, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import apps, shell
    from annotoolbox.suite.projectpanel import CProjectDialog

    app = QApplication.instance() or QApplication([])
    _mod, proj = imported(tmp_path)
    settings.project = str(proj.folder)
    dialog = CProjectDialog(shell.CSuiteWindow([], settings, tmp_path / "settings.json"), apps.parts())
    take, undo = dialog.m_buttons["Hand the patch files to the Assets app..."], dialog.m_buttons["Undo the hand-over"]
    assert take.isEnabled() and not undo.isEnabled()
    dialog.take_handover(is_waiting=True, confirm=lambda _p, _lines: False)
    assert "Nothing was handed over" in dialog.m_log.toPlainText() and take.isEnabled()
    dialog.take_handover(is_waiting=True, confirm=lambda _p, lines: bool(lines))
    app.processEvents()
    log = dialog.m_log.toPlainText()
    assert "Lost by the hand-over" in log and "Handed over: 3 files" in log
    assert not take.isEnabled() and undo.isEnabled() and handover.state(proj) is not None
    dialog.undo_handover(confirm=lambda _p, _what: True)
    assert "Hand-over undone: 3 files" in dialog.m_log.toPlainText() and take.isEnabled()
    assert importmeta.read_meta(proj.folder) is not None
