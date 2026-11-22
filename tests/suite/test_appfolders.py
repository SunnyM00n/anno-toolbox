"""The whole-file apps take the files of an imported mod (suite/appfolders.py): the folder table, the import placing
files in the apps' folders, the imported file as the base and not a change, one owner per path, the round trip."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from annotoolbox.objbuild import projectpart as objects
from annotoolbox.particleedit import projectpart as particles
from annotoolbox.soundedit import projectpart as sound
from annotoolbox.sources.gametree import CGameTree
from annotoolbox.suite import appfolders, carriedpart, importmeta, project, projectimport
from annotoolbox.suite.settings import CSettings
from annotoolbox.uiedit import projectpart as ui

SCENE = "data/ui/studio/generated/scene"
FILES: dict[str, bytes] = {
    "modinfo.json": json.dumps({"Anno": 8, "ModID": "take-mod", "Version": "1.0.0"}).encode(),
    "data/graphics/a.cfg": b"<Config>a</Config>",
    "data/graphics/b.ifo": b"<Info/>",
    "data/graphics/c.fc.xml": b"<ModOps/>",
    SCENE: b"phx scene",
    "data/sound/b.bnk": b"bank bytes",
    "data/sound/clip.wem": b"clip",
    "data/effects/e.rdp": b"effect",
    "data/graphics/x.rdm": b"model",
    "data/graphics/x.dds": b"DDS ",
    "nested/inner/modinfo.json": json.dumps({"Anno": 8, "ModID": "inner-mod"}).encode(),
    "nested/inner/data/n.cfg": b"nested config",
}
APP_PARTS = [carriedpart.PART, objects.PART, ui.UI_PART, sound.PART, particles.PART]


def make_mod(root: Path) -> Path:
    for rel, data in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


def do_import(tmp_path: Path, **options):
    mod = make_mod(tmp_path / "mod")
    folder = tmp_path / "proj"
    report = projectimport.import_mod(mod, folder, None, lambda _t: None, **options)
    return mod, project.CProject.open(folder), report


def build(proj: project.CProject) -> project.SBuildResult:
    return project.build(proj, APP_PARTS, CSettings(), lambda _t: None)


def test_the_table_names_each_apps_folder_and_leaves_the_rest_carried():
    assert appfolders.owner_of("anno117", "data/graphics/a.cfg") == "objects"
    assert appfolders.owner_of("anno117", "data/graphics/a.fc.xml") == "objects"
    assert appfolders.owner_of("anno117", SCENE) == "ui"
    assert appfolders.owner_of("anno117", "data/x/b.bnk") == "sound"
    assert appfolders.owner_of("anno117", "data/x/e.rdp") == "particles"
    for rel in ("data/x/m.rdm", "data/x/t.dds", "data/x/a.ctt", "data/x/assets.xml", "data/x/m.ani"):
        assert appfolders.owner_of("anno117", rel) == "carried"  # models and textures: no app edits the file
    assert appfolders.owner_of("anno1800", SCENE) == "carried"  # 1800's UI is not Phoenix files
    assert appfolders.owner_of("anno117", "nested/inner/data/n.cfg", ("nested/inner",)) == "carried"
    assert appfolders.root_for("p", "anno117", "objects") == Path("p/object-edits/anno117")
    assert appfolders.root_for("p", "anno117", "carried") == Path("p/mod-files/anno117")


def test_the_import_puts_each_file_into_its_apps_folder_and_the_build_is_identical(tmp_path):
    mod, proj, report = do_import(tmp_path)
    here = proj.folder
    assert (here / "object-edits/anno117/data/graphics/a.cfg").read_bytes() == b"<Config>a</Config>"
    assert (here / "object-edits/anno117/data/graphics/c.fc.xml").is_file()
    assert (here / "ui-edits/anno117" / SCENE).is_file()
    assert (here / "sound-edits/anno117/data/sound/clip.wem").is_file()
    assert (here / "particle-edits/anno117/data/effects/e.rdp").is_file()
    carried = carriedpart.files_of(carriedpart.carried_root(proj))
    assert set(carried) == {
        "data/graphics/x.rdm",
        "data/graphics/x.dds",
        "nested/inner/data/n.cfg",
        "nested/inner/modinfo.json",
    }
    assert dict(report.taken) == {"objects": 3, "ui": 1, "sound": 2, "particles": 1}
    assert importmeta.read_meta(here).taken == dict(report.taken)
    assert any("object" in line.lower() or "objects 3" in line for line in report.lines())
    build(proj)
    diff = carriedpart.compare_trees(mod, proj.build_dir)
    assert diff.identical, diff.lines()


def test_a_copy_import_with_no_hashes_takes_the_files_too(tmp_path):
    mod, proj, _report = do_import(tmp_path, use_links=False, is_hashed=False)
    assert (proj.folder / "object-edits/anno117/data/graphics/b.ifo").is_file()
    build(proj)
    assert carriedpart.compare_trees(mod, proj.build_dir).identical


def test_each_path_is_written_by_one_part(tmp_path):
    _mod, proj, _report = do_import(tmp_path)
    twin = carriedpart.carried_root(proj) / "data/graphics/a.cfg"  # a leftover copy beside the app's file
    twin.parent.mkdir(parents=True, exist_ok=True)
    twin.write_bytes(b"older")
    outputs = [(p.key, p.render(proj, CSettings(), lambda _t: None)) for p in APP_PARTS if p.has_work(proj)]
    owners = [key for key, files in outputs if "data/graphics/a.cfg" in files]
    assert owners == ["objects"]
    build(proj)  # no clash between the leftover and the app's file


def test_an_imported_file_is_the_base_and_a_saved_change_is_the_edit(tmp_path):
    _mod, proj, _report = do_import(tmp_path)
    assert objects.EDITS.saved_files(proj)  # the app reads what the folder holds over the game's files
    assert appfolders.edited(proj, objects.EDITS) == {}
    base = CGameTree(
        "g", lambda k: b"game " + k.encode(), ["data/graphics/a.cfg", "data/other.cfg"], lambda _k: "g", "anno117"
    )
    tree = appfolders.laid(base, proj, objects.EDITS)
    assert tree.read("data/graphics/a.cfg") == b"<Config>a</Config>"  # the imported file replaces the game's
    assert tree.read("data/other.cfg") == b"game data/other.cfg"
    assert tree.exists("data/graphics/b.ifo")  # a file the mod added shows too
    objects.EDITS.save_files(proj, "anno117", {"data/graphics/a.cfg": b"<Config>edited</Config>"})
    assert list(appfolders.edited(proj, objects.EDITS)) == ["data/graphics/a.cfg"]
    again = appfolders.laid(base, proj, objects.EDITS)
    assert again.read("data/graphics/a.cfg") == b"game data/graphics/a.cfg"  # the edit is the app's work, not the base
    assert again.read("data/graphics/b.ifo") == b"<Info/>"


def test_an_edit_in_one_app_changes_only_that_file_of_the_build_and_never_the_source(tmp_path):
    mod, proj, _report = do_import(tmp_path)
    sound.save(proj, "anno117", {"data/sound/b.bnk": b"changed bank"})
    build(proj)
    diff = carriedpart.compare_trees(mod, proj.build_dir)
    assert diff.different == ["data/sound/b.bnk"]
    assert not diff.missing and not diff.extra
    assert (mod / "data/sound/b.bnk").read_bytes() == b"bank bytes"  # the link was replaced, not written through


def test_an_import_without_hashes_compares_sizes_only(tmp_path):
    _mod, proj, _report = do_import(tmp_path, is_hashed=False)
    assert appfolders.edited(proj, ui.EDITS) == {}
    ui.save_file(proj, "anno117", SCENE, b"phx scene")  # the same bytes: still the base
    assert appfolders.edited(proj, ui.EDITS) == {}
    ui.save_file(proj, "anno117", SCENE, b"phx scene, longer")
    assert list(appfolders.edited(proj, ui.EDITS)) == [SCENE]


def test_a_project_imported_before_the_apps_took_files_is_moved_in_place(tmp_path):
    mod, proj, _report = do_import(tmp_path)
    carried = carriedpart.carried_root(proj)
    for owner in appfolders.FOLDERS:  # the layout phase 1 made: everything in mod-files
        root = appfolders.root_for(proj.folder, proj.game, owner)
        for path in sorted(root.rglob("*")):
            if path.is_file():
                target = carried / path.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, target)
    assert not any(objects.EDITS.saved_files(proj))
    moved = appfolders.take_into_apps(proj)
    assert dict(moved) == {"objects": 3, "ui": 1, "sound": 2, "particles": 1}
    assert "data/graphics/a.cfg" not in carriedpart.files_of(carried)
    assert "nested/inner/data/n.cfg" in carriedpart.files_of(carried)
    assert appfolders.take_into_apps(proj) == {}  # nothing left to move
    build(proj)
    assert carriedpart.compare_trees(mod, proj.build_dir).identical


@pytest.mark.parametrize("rel", ["data/graphics/a.cfg", "data/graphics/A.CFG"])
def test_the_held_paths_are_compared_without_regard_to_case(tmp_path, rel):
    _mod, proj, _report = do_import(tmp_path)
    assert "data/graphics/a.cfg" in appfolders.held_paths(proj)
    assert rel.lower() in appfolders.held_paths(proj)


def test_the_hash_answers_of_checked_files_stay_bounded(tmp_path):
    import hashlib

    file = tmp_path / "a.cfg"
    file.write_bytes(b"abc")
    base = {"a.cfg": (3, hashlib.sha256(b"abc").hexdigest())}
    info = appfolders._hash_matches.cache_info()
    for i in range(info.maxsize + 50):  # a new stamp each time: an unbounded dict would hold every one
        os.utime(file, ns=(10**9 + i, 10**9 + i))
        assert appfolders.is_base(base, "a.cfg", file)
    assert appfolders._hash_matches.cache_info().currsize <= info.maxsize
