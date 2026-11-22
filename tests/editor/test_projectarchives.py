"""The project's build for the games that load archives (editor/archivedelivery.py): the parts' files packed as the game
loads them, installed with the game's own files kept; the islands' and the 2205 UI's parts."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.editor.editorfakes import ISLE, edits, entries, install_1404
from tests.formats.isdfakes import SESSION2205_GAMEDATA as GAMEDATA
from tests.formats.isdfakes import file_db_index

from annotoolbox.editor import archivedelivery, oldenginebuild
from annotoolbox.editor import projectpart as islandpart
from annotoolbox.editor.formats.a7m import edit2205 as a7m_edit2205
from annotoolbox.editor.formats.isd import build as isd_build
from annotoolbox.flashui import projectpart as uipart
from annotoolbox.formats import rda
from annotoolbox.suite import project
from annotoolbox.suite.settings import CSettings


def _part(title: str, files: dict[str, bytes]) -> project.SPart:
    return project.SPart(title.lower(), title, lambda _p: True, lambda _p, _s, _log: dict(files))


def _project(tmp_path: Path, game: str) -> project.CProject:
    return project.CProject(tmp_path / "proj", mod_id="mine", game=game)


def test_2070_files_of_every_app_become_one_patch_over_the_games_own(tmp_path: Path):
    game = tmp_path / "game"
    (game / "maindata").mkdir(parents=True)
    (game / isd_build.PATCH).write_bytes(rda.write_v20({"data/credits.txt": b"credits", "data/a.txt": b"old"}))
    settings = CSettings(anno2070=str(game), workspace=str(tmp_path / "ws"))
    p = _project(tmp_path, "anno2070")
    parts = [_part("Islands", {"data/levels/x.isd": b"isd"}), _part("Assets", {"data/a.txt": b"new"})]
    result = project.build(p, parts, settings, lambda _t: None, archivedelivery.DELIVERIES)
    assert result.parts == ["Islands", "Assets"] and (p.build_dir / isd_build.PATCH).is_file()
    patch = entries(p.build_dir / isd_build.PATCH)
    assert (
        patch["data/credits.txt"] == b"credits"
        and patch["data/a.txt"] == b"new"
        and patch["data/levels/x.isd"] == b"isd"
    )
    assert not (p.build_dir / "modinfo.json").exists()  # an archive, not a mod folder
    project.install_built(p, settings, archivedelivery.DELIVERIES, lambda _t: None)
    assert isd_build.is_ours(game / isd_build.PATCH)
    kept = archivedelivery.patch_backup(settings)
    assert entries(kept)["data/a.txt"] == b"old"  # the game's own file, kept before ours replaced it


def test_the_games_copies_live_outside_the_workspace_and_an_old_one_is_moved_out_of_it(tmp_path: Path):
    game = tmp_path / "game"
    (game / "maindata").mkdir(parents=True)
    (game / isd_build.PATCH).write_bytes(rda.write_v20({isd_build.MARKER: b"ours"}))  # an install of ours
    own = rda.write_v20({"data/credits.txt": b"credits"})
    old = tmp_path / "ws" / "anno2070-patch9-original.rda"
    old.parent.mkdir()
    old.write_bytes(own)
    settings = CSettings(anno2070=str(game), workspace=str(tmp_path / "ws"))
    kept = archivedelivery.patch_backup(settings)
    assert kept.read_bytes() == own and not old.exists() and settings.workspace not in str(kept)
    settings.workspace = str(tmp_path / "another-workspace")  # the copy stays found
    assert archivedelivery.patch_backup(settings) == kept
    p = _project(tmp_path, "anno2070")
    project.build(
        p, [_part("Islands", {"data/levels/x.isd": b"isd"})], settings, lambda _t: None, archivedelivery.DELIVERIES
    )
    assert entries(p.build_dir / isd_build.PATCH)["data/credits.txt"] == b"credits"  # the game's own, not ours


def test_two_apps_writing_one_file_of_an_older_game_differently_is_refused(tmp_path: Path):
    p = _project(tmp_path, "anno2070")
    parts = [_part("Islands", {"data/a.txt": b"1"}), _part("Assets", {"data/A.txt": b"2"})]
    with pytest.raises(project.CClash, match="(?i)a.txt"):
        project.build(p, parts, CSettings(workspace=str(tmp_path)), lambda _t: None, archivedelivery.DELIVERIES)


def test_a_build_for_an_older_game_needs_its_delivery(tmp_path: Path):
    with pytest.raises(ValueError, match="not available"):
        project.build(_project(tmp_path, "anno1404"), [], CSettings(), lambda _t: None, {})


def test_1404_islands_and_asset_files_go_into_the_last_data_archive_of_their_group(tmp_path: Path):
    game = install_1404(tmp_path / "game")
    settings = CSettings(anno1404=str(game), workspace=str(tmp_path / "ws"))
    p = _project(tmp_path, "anno1404")
    p.island_edits.mkdir(parents=True)
    edits(p.island_edits, "1404_", ISLE, objects={"2070:12": {"position": [3.0, 1.0, 4.0]}})
    assert islandpart.PART.has_work(p)
    parts = [islandpart.PART, _part("Assets", {"data/a.txt": b"edited"})]
    project.build(p, parts, settings, lambda _t: None, archivedelivery.DELIVERIES)
    built = entries(p.build_dir / "maindata/data2.rda")
    assert built["data/a.txt"] == b"edited" and ISLE in built and built["data/system/shader.fx"] == b"fx"
    project.install_built(p, settings, archivedelivery.DELIVERIES, lambda _t: None)
    assert oldenginebuild.is_ours(game / "maindata/data2.rda")
    original = archivedelivery.old_engine_backups(settings, "anno1404") / oldenginebuild.backup_name(
        "anno1404", game / "maindata/data2.rda"
    )
    assert entries(original) == {"data/system/shader.fx": b"fx"}


def test_2205_files_are_pointed_at_in_the_archive_index_and_installed_with_the_games_own_kept(tmp_path: Path):
    game = tmp_path / "game"
    (game / "maindata").mkdir(parents=True)
    (game / "Bin/Win64").mkdir(parents=True)
    (game / "Bin/Win64/Anno2205.exe").write_bytes(b"")
    (game / "maindata/file.db").write_bytes(file_db_index())
    settings = CSettings(anno2205=str(game), patch2205=str(game), workspace=str(tmp_path / "ws"))
    p = _project(tmp_path, "anno2205")
    project.build(p, [_part("Islands", {GAMEDATA: b"gamedata"})], settings, lambda _t: None, archivedelivery.DELIVERIES)
    assert entries(p.build_dir / a7m_edit2205.ARCHIVE) == {GAMEDATA: b"gamedata"}
    assert a7m_edit2205.is_ours((p.build_dir / a7m_edit2205.FILE_DB).read_bytes())
    project.install_built(p, settings, archivedelivery.DELIVERIES, lambda _t: None)
    assert archivedelivery.index_backup(settings).read_bytes() == file_db_index()
    with pytest.raises(ValueError, match="no entry"):  # a file the game's index does not list cannot change
        project.build(
            p, [_part("Assets", {"data/nothing.xml": b"x"})], settings, lambda _t: None, archivedelivery.DELIVERIES
        )


def test_the_ui_part_has_work_only_for_a_2205_project_with_an_edit_file(tmp_path: Path):
    p = _project(tmp_path, "anno2205")
    assert not uipart.has_work(p)
    uipart.edit_file(p).parent.mkdir(parents=True)
    uipart.edit_file(p).write_text('{"format": 1, "game": "anno2205", "files": {"data/ui/x.png": "x.png"}}', "utf-8")
    assert uipart.has_work(p)
    assert not uipart.has_work(_project(tmp_path, "anno117"))
