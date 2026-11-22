"""An imported mod's maps and islands (docs/mod-import.md section 13): the files move into the Map editor's and the
island editor's folders, the apps open them as the base and not as a change, an edit changes only its own files, and the
round trip stays identical, with one owner for every path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.fakes import FakeFiles
from tests.islands.islandfakes import island117_bytes as _island
from tests.mapedit.mapeditfakes import mapedit_game as _game
from tests.mapedit.mapeditfakes import session_bytes, template_bytes

from annotoolbox.editor import editorapp
from annotoolbox.editor import projectpart as islands
from annotoolbox.islands import islandedits
from annotoolbox.islands.islandview import CFiles
from annotoolbox.mapedit import levelfile, mapdoc, pool
from annotoolbox.mapedit import projectpart as maps
from annotoolbox.sources import gametree
from annotoolbox.suite import appfolders, carriedpart, importmeta, modclassify, project, projectimport
from annotoolbox.suite.settings import CSettings

STEM = "data/base/provinces/roman/templates/pool/t_01/t_01"
MAP = STEM + ".a7tinfo"
ISLE = "data/base/provinces/roman/islands/pool/isle/isle.a7m"
PAGE = "data/base/provinces/roman/islands/pool/isle/_gamedata/isle/page.ctt"
POOL_ASSETS = "data/base/config/export/assets.xml"
A7M, A7MINFO = _island()
FILES: dict[str, bytes] = {
    "modinfo.json": json.dumps({"Anno": 8, "ModID": "maps-mod", "Version": "1.0.0"}).encode(),
    MAP: template_bytes(),
    STEM + ".a7t": session_bytes(),
    STEM + ".a7te": levelfile.editor_level(64),
    ISLE: A7M,
    ISLE[:-4] + ".a7minfo": A7MINFO,
    ISLE[:-4] + ".a7me": b"editor header",
    PAGE: b"terrain page",
    PAGE[:-4] + ".tmc": b"terrain map",
    POOL_ASSETS: b"<ModOps>\n</ModOps>\n",
    "data/graphics/x.rdm": b"model",
    "data/loose/a.ctt": b"not an island page",
    "nested/inner/modinfo.json": json.dumps({"Anno": 8, "ModID": "inner-mod"}).encode(),
    "nested/inner/data/p/_gamedata/z/z.ctt": b"nested page",
}


def make_mod(root: Path) -> Path:
    for rel, data in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


def parts() -> list:
    return [carriedpart.PART, islands.PART, maps.PART]


def do_import(tmp_path: Path, **options):
    mod = make_mod(tmp_path / "mod")
    folder = tmp_path / "proj"
    report = projectimport.import_mod(mod, folder, None, lambda _t: None, **options)
    return mod, project.CProject.open(folder), report


def build(proj: project.CProject) -> project.SBuildResult:
    return project.build(proj, parts(), CSettings(), lambda _t: None)


@pytest.fixture
def fake_game(monkeypatch):
    """The island build reads the project's islands over a fake game (no install needed)."""

    def files(proj, _settings):
        root = islands.base_folder(proj) if islands.has_base(proj) else None
        return CFiles(FakeFiles({POOL_ASSETS: b"<Assets/>"}), root)

    monkeypatch.setattr(islands, "_files", files)


def test_the_sort_sends_a_map_with_its_level_file_and_an_island_with_its_pages_to_their_apps():
    owners = {
        MAP: "maps",
        STEM + ".a7t": "maps",
        STEM + ".a7te": "maps",
        ISLE: "islands",
        ISLE[:-4] + ".a7minfo": "islands",
        ISLE[:-4] + ".a7me": "islands",
        PAGE: "islands",
        PAGE[:-4] + ".tmc": "islands",
        "data/loose/a.ctt": "carried",  # a page outside an island's folder is not one of its pages
    }
    for rel, owner in owners.items():
        assert appfolders.owner_of("anno117", rel) == owner, rel
    assert appfolders.owner_of("anno117", PAGE, ("nested/inner",)) == "islands"
    assert appfolders.owner_of("anno117", "nested/inner/" + PAGE, ("nested/inner",)) == "carried"
    assert appfolders.owner_of("anno1800", MAP) == "maps"
    assert appfolders.owner_of("anno1800", ISLE) == "carried"  # 1800's island build reads the game, not the project
    assert modclassify.classify("anno117", PAGE).kind == "terrain"


def test_the_import_puts_maps_and_islands_into_the_apps_folders_and_the_round_trip_is_identical(tmp_path):
    mod, proj, report = do_import(tmp_path)
    here = proj.folder
    for rel in (MAP, STEM + ".a7t", STEM + ".a7te"):
        assert (here / "map-edits/anno117" / rel).read_bytes() == FILES[rel]
    for rel in (ISLE, ISLE[:-4] + ".a7minfo", ISLE[:-4] + ".a7me", PAGE, PAGE[:-4] + ".tmc"):
        assert (here / "island-edits/anno117" / rel).read_bytes() == FILES[rel]
    assert set(carriedpart.files_of(carriedpart.carried_root(proj))) == {
        POOL_ASSETS,
        "data/graphics/x.rdm",
        "data/loose/a.ctt",
        "nested/inner/modinfo.json",
        "nested/inner/data/p/_gamedata/z/z.ctt",
    }
    assert dict(report.taken) == {"maps": 3, "islands": 5}
    assert not list(here.glob("island-edits/*.json"))  # the base files are no edit file
    assert islands.has_work(proj) and maps.EDITS.has_work(proj)
    build(proj)
    diff = carriedpart.compare_trees(mod, proj.build_dir)
    assert diff.identical, diff.lines()


def test_every_path_has_one_owner_even_with_a_leftover_in_the_carried_folder(tmp_path):
    _mod, proj, _report = do_import(tmp_path)
    twin = carriedpart.carried_root(proj) / ISLE
    twin.parent.mkdir(parents=True, exist_ok=True)
    twin.write_bytes(b"older copy")
    held = appfolders.held_paths(proj)
    assert ISLE.lower() in held and (STEM + ".a7te").lower() in held
    outputs = {p.key: p.render(proj, CSettings(), lambda _t: None) for p in parts() if p.has_work(proj)}
    assert [k for k, files in outputs.items() if ISLE in files] == ["islands"]
    assert [k for k, files in outputs.items() if STEM + ".a7te" in files] == ["maps"]
    build(proj)


def test_a_project_imported_before_the_apps_took_maps_and_islands_is_moved_in_place(tmp_path):
    mod, proj, _report = do_import(tmp_path)
    carried = carriedpart.carried_root(proj)
    for owner in ("maps", "islands"):
        root = appfolders.root_for(proj.folder, proj.game, owner)
        for path in sorted(root.rglob("*")):
            if path.is_file():
                (carried / path.relative_to(root)).parent.mkdir(parents=True, exist_ok=True)
                path.replace(carried / path.relative_to(root))
    assert not maps.EDITS.saved_files(proj)
    assert dict(appfolders.take_into_apps(proj)) == {"maps": 3, "islands": 5}
    build(proj)
    assert carriedpart.compare_trees(mod, proj.build_dir).identical


def test_the_map_editor_opens_an_imported_map_from_the_project_as_its_base(tmp_path):
    mod, proj, _report = do_import(tmp_path)
    game = tmp_path / "game"
    _game(game)
    for suffix in (".a7tinfo", ".a7t", ".a7te"):
        (game / (STEM + suffix)).unlink()  # the game has the pool and the island, the map is the mod's own
    tree = gametree.loose_tree(game, "117")
    assert not tree.exists(MAP)
    tree = tree.over(maps.EDITS.folder(proj, "anno117"), "project")  # as the window's open_game lays it
    assert MAP in mapdoc.map_paths(tree, "anno117")
    doc = mapdoc.open_map(tree, "anno117", MAP, pool.CGameMaps.read(tree, "anno117"))
    assert not doc.changed() and doc.render() == FILES[MAP]  # the imported file is the base, not a change
    assert tree.read(STEM + ".a7t") == FILES[STEM + ".a7t"]
    doc.move([1], 8, -8)
    maps.save(proj, "anno117", {MAP: doc.render()})
    assert appfolders.edited(proj, maps.EDITS) == {MAP: maps.EDITS.folder(proj, "anno117") / MAP}
    build(proj)
    diff = carriedpart.compare_trees(mod, proj.build_dir)
    assert diff.different == [MAP] and not diff.missing and not diff.extra
    assert (mod / MAP).read_bytes() == FILES[MAP]  # the source is never written through the link


def test_a_grown_maps_level_file_replaces_the_imported_one_without_a_second_writer(tmp_path):
    mod, proj, _report = do_import(tmp_path)
    maps.save(proj, "anno117", {STEM + ".a7te": b"grown level", STEM + ".a7t": b"grown session"})
    build(proj)
    diff = carriedpart.compare_trees(mod, proj.build_dir)
    assert sorted(diff.different) == [STEM + ".a7t", STEM + ".a7te"] and not diff.missing and not diff.extra


def test_the_island_editor_lists_and_opens_an_imported_island_with_it_as_the_base(tmp_path, monkeypatch):
    _mod, proj, _report = do_import(tmp_path)
    settings = CSettings(workspace=str(tmp_path / "ws"), project=str(proj.folder))
    app = editorapp.CApp(tmp_path / "settings.json", settings=settings)
    monkeypatch.setattr(app, "game117", lambda: FakeFiles({}))
    assert app.project_islands() == [ISLE]  # the page and the info file are not islands
    seen = {}

    def opened(files, path, edits, kind, name, is_buildable, meta=None):
        seen.update(files=files, path=path, edits=edits, kind=kind, name=name, buildable=is_buildable, meta=meta)
        return {"kind": kind, "name": name, "path": path}

    monkeypatch.setattr(app, "_open117", opened)
    app.open_project_island(ISLE)
    assert seen["files"].m_mod_root == proj.folder / "island-edits/anno117"
    assert seen["files"].read(ISLE) == A7M  # the project's file, as imported
    assert seen["edits"] == proj.folder / "island-edits/117_isle.json"
    assert seen["meta"] == {"source": ISLE} and seen["buildable"] and seen["kind"] == "anno117"


def test_an_edit_file_without_an_edit_leaves_the_island_as_imported(tmp_path, fake_game):
    mod, proj, _report = do_import(tmp_path)
    islandedits.save(proj.island_edits / "117_isle.json", {**islandedits.empty(), "source": ISLE})
    assert not islands.edits_something(islandedits.load(proj.island_edits / "117_isle.json"))
    build(proj)
    assert carriedpart.compare_trees(mod, proj.build_dir).identical


def test_an_edited_island_is_built_over_its_base_and_only_its_own_files_change(tmp_path, fake_game):
    mod, proj, _report = do_import(tmp_path)
    edits = islandedits.empty()
    edits["source"] = ISLE
    edits["objects"][islandedits.key("data/a/rock.cfg", (1, 0, 1))] = {"deleted": True}
    islandedits.save(proj.island_edits / "117_isle.json", edits)
    result = build(proj)
    diff = carriedpart.compare_trees(mod, proj.build_dir)
    assert diff.different == [ISLE] and not diff.missing and not diff.extra, diff.lines()
    assert (proj.build_dir / PAGE).read_bytes() == FILES[PAGE]  # the pages the edit did not change come from the base
    assert (mod / ISLE).read_bytes() == A7M and result.files
    assert islands.has_base(proj)


def test_a_project_without_an_import_keeps_its_islands_flow(tmp_path):
    proj = project.CProject.open(tmp_path / "plain")
    proj.save()
    assert not islands.has_base(proj) and not islands.has_work(proj)
    assert importmeta.read_meta(proj.folder) is None
