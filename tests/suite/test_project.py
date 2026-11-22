"""The suite's project (suite/project.py): one folder per mod, every app's share merged into one build, its install;
an older workspace opens as a project; the apps' parts (editor/projectpart, assetedit/projectpart)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.sources.sourcesfakes import asset_game

from annotoolbox.assetedit import projectpart as assetpart
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.changeset import CChangeSet
from annotoolbox.editor import editorapp
from annotoolbox.editor import projectpart as islandpart
from annotoolbox.suite import project
from annotoolbox.suite.settings import CSettings


def _ops(*bodies: str) -> bytes:
    return ("<ModOps>\n" + "\n".join(bodies) + "\n</ModOps>\n").encode()


def _part(key: str, files: dict[str, bytes], work: bool = True) -> project.SPart:
    return project.SPart(key, key.title(), lambda _p: work, lambda _p, _s, _log: dict(files))


def test_a_workspace_without_a_project_file_is_a_project_with_its_edit_files_where_they_were(tmp_path):
    ws = tmp_path / "My Workspace"
    p = project.CProject.open(ws)
    assert not p.defined and p.mod_id == "my-workspace" and p.guid_range == project.DEFAULT_RANGE
    assert p.island_edits == ws / "island-edits" and p.asset_edits == ws / "asset-edits"
    assert p.build_dir == ws / "build" / "my-workspace"


def test_a_project_file_round_trips_and_is_checked(tmp_path):
    p = project.CProject(tmp_path, mod_id="my-mod", name="My mod", version="1.2.0", game="anno1800",
                        guid_range=(1_900_000_000, 1_900_000_999))  # fmt: skip
    p.save()
    again = project.CProject.open(tmp_path)
    assert again.defined and (again.mod_id, again.game, again.guid_range) == ("my-mod", "anno1800", p.guid_range)
    for bad in ({"mod_id": "My Mod"}, {"guid_range": [5, 1]}, {"game": "anno2070"}, {"format": 99}):
        data = {**json.loads((tmp_path / project.FILE).read_text(encoding="utf-8")), **bad}
        (tmp_path / project.FILE).write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            project.CProject.open(tmp_path)


@pytest.mark.parametrize("bad", [{"dependencies": {"Require": 5}}, {"bundle": 5}, {"dependencies": {"Require": [[1]]}}])
def test_a_project_file_whose_lists_are_not_lists_is_a_value_error_not_a_crash(tmp_path, bad):
    project.CProject(tmp_path, mod_id="my-mod", name="My mod").save()
    data = {**json.loads((tmp_path / project.FILE).read_text(encoding="utf-8")), **bad}
    (tmp_path / project.FILE).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        project.CProject.open(tmp_path)


def test_a_project_file_with_a_value_of_the_wrong_kind_is_a_value_error_not_a_crash(tmp_path):
    for bad in ({"mod_id": None}, {"dependencies": 5}, {"bundle": 5}):
        (tmp_path / project.FILE).write_text(json.dumps({"mod_id": "a-b", **bad}), encoding="utf-8")
        with pytest.raises(ValueError):
            project.CProject.open(tmp_path)


def test_the_modops_files_of_two_parts_become_one_in_the_parts_order():
    a = {"data/base/config/export/assets.xml": _ops('<ModOp Type="add" Path="/A" />'), "x.txt": b"same"}
    b = {"data/base/config/export/assets.xml": _ops('<ModOp Type="merge" Path="/B" />'), "x.txt": b"same"}
    merged = project.merge_files([("Islands", a), ("Assets", b)])
    text = merged["data/base/config/export/assets.xml"].decode()
    assert text.count("<ModOps>") == 1 and text.index('Path="/A"') < text.index("<!-- Assets -->") < text.index('"/B"')
    assert merged["x.txt"] == b"same"
    with pytest.raises(project.CClash, match="x.txt"):
        project.merge_files([("Islands", {"x.txt": b"one"}), ("Assets", {"x.txt": b"two"})])


def test_the_build_writes_one_mod_with_one_modinfo_and_installs_it(tmp_path):
    p = project.CProject(tmp_path / "proj", mod_id="one-mod", name="One mod")
    p.save()
    parts = [
        _part("islands", {"data/base/config/export/assets.xml": _ops('<ModOp Type="add" Path="/A" />')}),
        _part("assets", {"data/base/config/export/assets.xml": _ops('<ModOp Type="add" Path="/B" />'),
                         "modinfo.json": b"{}"}),
        _part("idle", {"never.txt": b""}, work=False),
    ]  # fmt: skip
    (p.build_dir / "stale.txt").parent.mkdir(parents=True)
    (p.build_dir / "stale.txt").write_text("from an older build", encoding="utf-8")
    result = project.build(p, parts, CSettings(), log=lambda _t: None)
    info = json.loads((p.build_dir / "modinfo.json").read_text(encoding="utf-8"))
    assert info["ModID"] == "one-mod" and result.parts == ["Islands", "Assets"] and result.files == 1
    assert not (p.build_dir / "stale.txt").exists() and not (p.build_dir / "never.txt").exists()
    with pytest.raises(RuntimeError, match="absolute"):
        project.install(p, Path("mods"), log=lambda _t: None)
    target = project.install(p, tmp_path / "mods", log=lambda _t: None)
    assert target == tmp_path / "mods" / "one-mod" and (target / "modinfo.json").is_file()


def test_the_island_editor_and_the_asset_editor_keep_their_work_in_the_open_project(tmp_path):
    settings = CSettings(workspace=str(tmp_path / "ws"))
    settings.save(tmp_path / "settings.json")
    app = editorapp.CApp(tmp_path / "settings.json", settings=settings)
    assert app.edits_dir == tmp_path / "ws" / "island-edits"  # no project open: the workspace, as before
    settings.project = str(tmp_path / "proj")
    assert app.edits_dir == tmp_path / "proj" / "island-edits"
    p = project.CProject(tmp_path / "proj", mod_id="proj", name="Proj", guid_range=(1_500_000_000, 1_500_000_100))
    assert CChangeSet.for_project("anno117", p).m_guid_range == project.DEFAULT_RANGE  # not saved: the defaults
    p.save()
    made = CChangeSet.for_project("anno117", project.current(settings))
    assert (made.m_mod_id, made.m_guid_range) == ("proj", (1_500_000_000, 1_500_000_100))


def test_the_island_part_has_work_when_an_edit_file_of_the_projects_game_is_there(tmp_path):
    p = project.CProject(tmp_path, mod_id="p", game="anno117")
    assert not islandpart.has_work(p)
    p.island_edits.mkdir()
    (p.island_edits / "1800_some_island.json").write_text("{}", encoding="utf-8")
    assert not islandpart.has_work(p)
    (p.island_edits / "117_some_island.json").write_text("{}", encoding="utf-8")
    assert islandpart.has_work(p)


def test_the_asset_part_writes_the_change_set_of_the_projects_game_as_modops(tmp_path):
    game_dir = asset_game(tmp_path / "117")
    settings = CSettings(anno117=str(game_dir), workspace=str(tmp_path / "ws"))
    p = project.CProject(tmp_path / "proj", mod_id="proj", name="Proj")
    assert not assetpart.has_work(p)
    doc = open_game("anno117", game_dir, tmp_path / "cache").document(CChangeSet.for_project("anno117", p))
    doc.set_value(2002, "Building/Upkeep", "11")
    doc.save(assetpart.change_set_path(p))
    assert assetpart.has_work(p)
    result = project.build(p, [islandpart.PART, assetpart.PART], settings, log=lambda _t: None)
    patch = (p.build_dir / "data/base/config/export/assets.xml").read_text(encoding="utf-8")
    assert result.parts == ["Assets"] and "<Upkeep>11</Upkeep>" in patch.replace(" ", "")


def _built(tmp_path, mod_id="one-mod"):
    p = project.CProject(tmp_path / "proj", mod_id=mod_id, name="One mod")
    p.save()
    project.build(p, [_part("a", {"data/f.txt": b"new"})], CSettings(), log=lambda _t: None)
    return p


def test_install_refuses_to_replace_another_mod_and_leaves_it_alone(tmp_path):
    from tests.modmgr.modmgrfakes import make_mod

    p, mods = _built(tmp_path, "my-mod"), tmp_path / "mods"
    make_mod(mods, "my-mod", mod_id="someone-else")
    with pytest.raises(ValueError, match="another mod"):
        project.install(p, mods, log=lambda _t: None, trash=lambda _p: None)
    assert (mods / "my-mod" / "modinfo.json").is_file() and not (mods / "my-mod" / "data").exists()


def test_a_failing_copy_leaves_the_installed_copy_intact_and_the_old_one_is_binned_last(tmp_path, monkeypatch):
    import shutil

    p, mods = _built(tmp_path), tmp_path / "mods"
    project.install(p, mods, log=lambda _t: None, trash=lambda _p: None)
    (mods / "one-mod" / "marker.txt").write_text("old", encoding="utf-8")
    real = shutil.copytree

    def half(src, dst, *a, **k):
        real(src, dst, *a, **k)
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", half)
    with pytest.raises(ValueError, match="could not be copied"):
        project.install(p, mods, log=lambda _t: None, trash=lambda _p: None)
    assert (mods / "one-mod" / "marker.txt").is_file() and [d.name for d in mods.iterdir()] == ["one-mod"]
    monkeypatch.undo()
    binned = []
    project.install(
        p, mods, log=lambda _t: None, trash=lambda path: binned.append((Path(path) / "marker.txt").is_file())
    )
    assert binned == [True] and not (mods / "one-mod" / "marker.txt").exists()


def test_a_project_build_looks_for_its_dependencies_in_the_documents_mods_folder(tmp_path, monkeypatch):
    from tests.modmgr.modmgrfakes import make_mod

    docs = tmp_path / "docs"
    make_mod(docs / "Anno 117 - Pax Romana" / "mods", "lib")
    monkeypatch.setattr(project.gametree, "documents_dir", lambda: docs)
    p = project.CProject(tmp_path / "proj", mod_id="dep-mod", name="Dep", dependencies={"Require": ["lib"]})
    p.save()
    result = project.build(p, [_part("a", {"data/f.txt": b"x"})], CSettings(mods=""), log=lambda _t: None)
    assert not [t for t in result.problems if "M01" in t]
