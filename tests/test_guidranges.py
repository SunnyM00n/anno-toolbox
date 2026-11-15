"""The three ways to a GUID range (suite/guidranges.py): own, publicly declared, typed in; the checks against the
registry, the game's own GUIDs and the user's other projects; the choice kept in the project and the change set."""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from tests.fakes import asset_game

from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.changeset import ChangeSet
from annotoolbox.suite import guidranges as gr
from annotoolbox.suite import islandguids, project
from annotoolbox.suite.settings import Settings


def test_the_shipped_data_is_whole():
    for game in ("anno117", "anno1800"):
        info = gr.game_data(game)
        assert info["safe"] == [1_337_471_142, 2_147_483_647] and info["registry"]["url"] and info["claim"]
        rows = gr.published(game)
        assert len(rows) > 50 and all(a <= b for a, b, _w, _k in rows)
        assert {k for *_x, k in rows} <= {"claim", "reserved", "personal"}
    assert gr.public_choice("anno117", gr.PERSONAL_ID).span == (2_001_000_000, 2_001_009_999)
    for runs in gr.data()["vanilla"]["games"].values():
        assert (
            runs["runs"] and all(a <= b for a, b in runs["runs"]) and runs["max"] < gr.game_data("anno117")["safe"][0]
        )


def test_the_old_default_range_belongs_to_a_modder_in_the_registry():
    problems = gr.check("anno117", project.DEFAULT_RANGE)
    assert any("LQ" in p.text for p in problems) and not gr.blocking(problems)


def test_the_public_choices_are_free_in_the_registry_and_of_the_game():
    choices = gr.public_choices("anno117")
    assert choices[0].key == gr.PERSONAL_ID and len(choices) > 5
    for c in choices[1:]:
        assert c.span[1] - c.span[0] + 1 == gr.BLOCK and c.claim_url and "claim" in c.note
        assert not [p for p in gr.check("anno117", c.span) if "registry gives" in p.text or "reserves" in p.text]
        assert not [p for p in gr.check("anno1800", c.span) if "registry gives" in p.text]
    old = gr.public_choices("anno2070")  # no registry: a range above the game's own GUIDs
    assert old and not gr.blocking(gr.check("anno2070", old[0].span))


def test_the_own_range_is_made_once_outside_everything_and_kept_apart_per_project():
    class Fake:
        own_reserve: list = []

    s = Fake()
    reserve = gr.own_reserve(s)
    assert reserve == tuple(s.own_reserve) and reserve[1] - reserve[0] + 1 == gr.RESERVE_SIZE
    assert not gr.check("anno117", reserve) and not gr.check("anno1800", reserve)
    first = gr.own_range(s)
    second = gr.own_range(s, {"a": first})
    assert first[0] == reserve[0] and second[0] == first[1] + 1 and gr.own_reserve(s) == reserve
    used = {str(i): (reserve[0] + i * gr.OWN_SIZE, reserve[0] + (i + 1) * gr.OWN_SIZE - 1) for i in range(10)}
    with pytest.raises(ValueError, match="used up"):
        gr.own_range(s, used)


def test_a_typed_range_is_checked_against_the_game_the_registry_and_the_other_projects():
    assert gr.blocking(gr.check("anno117", (5_500_000, 5_500_099)))  # inside a cluster of vanilla GUIDs
    assert gr.blocking(gr.check("anno117", (9, 3)))
    low = gr.check("anno117", (3_000_000, 3_000_099))  # free of vanilla GUIDs but outside Ubisoft's safe range
    assert [p.level for p in low] == [gr.WARNING] and "keeps for mods" in low[0].text
    taken = gr.check("anno117", (1_500_000_000, 1_500_000_100))
    assert any("Serp's Mods" in p.text for p in taken)
    mine = gr.check("anno117", (1_337_530_000, 1_337_530_099), {"other": (1_337_530_050, 1_337_530_060)})
    assert [p.text for p in mine if "your project other" in p.text]
    assert not gr.check("anno117", (1_337_530_000, 1_337_530_099))
    exact = {7, 9}  # the game's exact GUIDs, when the caller has them: a gap inside a measured cluster is fine
    assert not gr.blocking(gr.check("anno117", (5_500_000, 5_500_099), vanilla=exact))


def test_resolve_turns_a_choice_into_numbers():
    class Fake:
        own_reserve: list = []

    s = Fake()
    assert gr.resolve(gr.CUSTOM, "", (4, 5), s, "anno117") == (4, 5)
    assert gr.resolve(gr.PUBLIC, gr.PERSONAL_ID, (0, 0), s, "anno117") == (2_001_000_000, 2_001_009_999)
    assert gr.resolve(gr.OWN, "", (0, 0), s, "anno117") == gr.own_range(s)
    for bad in (("public", "nope"), ("other", "")):
        with pytest.raises(ValueError):
            gr.resolve(bad[0], bad[1], (0, 0), s, "anno117")


def test_the_choice_is_kept_in_the_project_and_older_files_read_as_typed(tmp_path):
    s = Settings()
    p = project.Project(tmp_path / "a", mod_id="a")
    p.choose_range(gr.OWN, "", p.guid_range, s)
    p.save()
    again = project.Project.open(tmp_path / "a")
    assert (again.guid_choice, again.guid_range) == (
        gr.OWN,
        tuple(s.own_reserve[:1]) + (s.own_reserve[0] + 99_999,),
    )
    again.choose_range(gr.PUBLIC, gr.PERSONAL_ID, again.guid_range, s)
    assert again.guid_range == (2_001_000_000, 2_001_009_999) and again.guid_source == gr.PERSONAL_ID
    again.choose_range(gr.CUSTOM, "", (7, 8), s)
    assert again.guid_source == ""
    data = json.loads((tmp_path / "a" / project.FILE).read_text(encoding="utf-8"))
    for key in ("guid_choice", "guid_source"):
        data.pop(key)
    (tmp_path / "a" / project.FILE).write_text(json.dumps(data), encoding="utf-8")
    assert project.Project.open(tmp_path / "a").guid_choice == gr.CUSTOM
    data["guid_choice"] = "nonsense"
    (tmp_path / "a" / project.FILE).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="choice"):
        project.Project.open(tmp_path / "a")


def test_a_second_own_range_project_takes_the_next_block_and_sees_the_first(tmp_path):
    s = Settings()
    first, second = (project.Project(tmp_path / n, mod_id=n) for n in ("one", "two"))
    first.choose_range(gr.OWN, "", first.guid_range, s)
    first.save()
    project.remember(s, first.folder)
    assert project.other_ranges(s, second.folder, "anno117") == {"one": first.guid_range}
    assert (
        project.other_ranges(s, second.folder, "anno1800") == {}
        and project.other_ranges(s, first.folder, "anno117") == {}
    )
    second.choose_range(gr.OWN, "", second.guid_range, s)
    assert second.guid_range[0] == first.guid_range[1] + 1


def test_a_range_cannot_move_away_from_islands_already_numbered(tmp_path):
    p = project.Project(tmp_path / "isl", mod_id="isl", guid_range=(1_337_530_000, 1_337_530_999))
    p.save()
    reg = islandguids._Registry(1_337_530_000, 1_337_530_999, tmp_path / "isl" / islandguids.FILE)
    reg.get("a.a7m")
    reg.save()
    assert islandguids.held(p.island_guids) == [1_337_530_000]
    p.guid_range = (1_337_540_000, 1_337_540_999)
    with pytest.raises(ValueError, match="numbered already"):
        p.save()
    p.guid_range = (1_337_529_000, 1_337_531_000)
    p.save()


def test_the_change_set_keeps_the_choice_and_takes_the_projects(tmp_path):
    c = ChangeSet(
        "anno117", guid_range=(1_337_530_000, 1_337_530_099), guid_choice=gr.PUBLIC, guid_source="free-1337530000"
    )
    again = ChangeSet.from_json(c.to_json())
    assert (again.guid_choice, again.guid_source, again.guid_range) == (gr.PUBLIC, "free-1337530000", c.guid_range)
    old = json.loads(c.to_json())
    old.pop("guid_choice")
    old.pop("guid_source")
    assert ChangeSet.from_json(json.dumps(old)).guid_choice == gr.CUSTOM
    p = project.Project(tmp_path / "p", mod_id="p", guid_range=(1, 5))
    p.choose_range(gr.PUBLIC, gr.PERSONAL_ID, (0, 0), Settings())
    p.save()
    made = ChangeSet.for_project("anno117", project.Project.open(p.folder))
    assert (made.guid_choice, made.guid_range) == (gr.PUBLIC, (2_001_000_000, 2_001_009_999))
    s = Settings()
    legacy = ChangeSet.for_project("anno117", project.Project.open(tmp_path / "workspace"), s)
    assert legacy.guid_choice == gr.OWN and legacy.guid_range[0] == s.own_reserve[0]
    assert (
        ChangeSet.for_project("anno117", project.Project.open(tmp_path / "workspace")).guid_range
        == project.DEFAULT_RANGE
    )


def test_the_document_sets_the_range_as_one_undoable_step(tmp_path):
    doc = open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache").document()
    before = (doc.changes.guid_range, doc.changes.guid_choice)
    doc.set_guid_range((1_337_530_000, 1_337_530_099), gr.PUBLIC, "free-1337530000")
    assert (
        doc.dirty and doc.changes.guid_range == (1_337_530_000, 1_337_530_099) and doc.changes.guid_choice == gr.PUBLIC
    )
    new = doc.new_asset("", "Thing", copy_of=1001)
    assert 1_337_530_000 <= new <= 1_337_530_099
    doc.undo.undo()
    doc.undo.undo()
    assert (doc.changes.guid_range, doc.changes.guid_choice) == before


def test_the_dialog_offers_the_three_ways_and_stops_the_game_s_own_numbers():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import guidrangedialog as gd

    app = QApplication.instance() or QApplication([])
    s = Settings()
    dialog = gd.GuidRangeDialog(None, "anno117", s, {}, gd.Selection(gr.OWN, "", (0, 0)))
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert dialog.selection().choice == gr.OWN and dialog.selection().span == gr.own_range(s) and ok.isEnabled()
    dialog.radios[gr.PUBLIC].setChecked(True)
    assert dialog.selection().span == (2_001_000_000, 2_001_009_999) and "WARNING" in dialog.status.text()
    assert "claim" in dialog.public_text.text() or dialog.choices[0].key == gr.PERSONAL_ID
    dialog.public.setCurrentIndex(1)
    assert "claim" in dialog.public_text.text() and "github.com/anno-mods/GuidRanges" in dialog.public_text.text()
    assert dialog.status.text().endswith("no clash found")
    dialog.radios[gr.CUSTOM].setChecked(True)
    dialog.first.setText("5,500,000")
    dialog.last.setText("5,500,099")
    assert not ok.isEnabled() and "ERROR" in dialog.status.text()
    dialog.first.setText("1,500,000,000")
    dialog.last.setText("1,500,000,100")
    assert ok.isEnabled() and "Serp's Mods" in dialog.status.text()
    dialog.first.setText("x")
    assert not ok.isEnabled()
    assert app is not None


def _settings(tmp_path):
    s = Settings(workspace=str(tmp_path / "ws"))
    s.own_reserve = [2_111_000_000, 2_111_999_999]
    return s


def test_the_workspaces_change_set_and_a_later_project_do_not_get_the_same_own_block(tmp_path):
    from annotoolbox.assetedit.changeset import ChangeSet
    from annotoolbox.suite import project as pr

    s = _settings(tmp_path)
    ws = pr.Project.open(tmp_path / "ws")
    made = ChangeSet.for_project("anno117", ws, s)
    made.save(ws.asset_edits / "anno117.json")
    p = pr.Project(tmp_path / "proj", mod_id="proj")
    p.choose_range(gr.OWN, "", (0, 0), s)
    assert p.guid_range[0] > made.guid_range[1] or p.guid_range[1] < made.guid_range[0]
    # and the other way round: a project first, then the workspace's change set
    p.save()
    pr.remember(s, p.folder)
    again = ChangeSet.for_project("anno117", ws, s)
    assert again.guid_range[0] > p.guid_range[1] or again.guid_range[1] < p.guid_range[0]


def test_two_known_projects_with_one_folder_name_both_count(tmp_path):
    from annotoolbox.suite import project as pr

    s = _settings(tmp_path)
    for parent, lo in (("a", 2_111_000_000), ("b", 2_111_100_000)):
        pr.Project(tmp_path / parent / "mod", mod_id="mod", guid_range=(lo, lo + 99_999)).save()
        pr.remember(s, tmp_path / parent / "mod")
    others = pr.other_ranges(s, tmp_path / "c" / "mod", "anno117")
    assert sorted(others.values()) == [(2_111_000_000, 2_111_099_999), (2_111_100_000, 2_111_199_999)]


def test_a_change_set_with_an_unknown_guid_choice_reads_as_custom_and_keeps_its_numbers():
    from annotoolbox.assetedit.changeset import ChangeSet

    got = ChangeSet.from_json('{"game": "anno117", "guid_choice": "foo", "guid_range": [5, 9]}')
    assert got.guid_choice == gr.CUSTOM and got.guid_range == (5, 9)
