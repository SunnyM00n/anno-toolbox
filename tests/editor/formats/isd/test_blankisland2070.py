"""A new 2070 island from scratch: named by its edit file, laid over the game's files for the editor and the build."""

import pytest
from tests.fakes import FakeTree
from tests.formats.isdfakes import install

from annotoolbox.editor import editorapp
from annotoolbox.editor.formats.isd import blank as isd_blank
from annotoolbox.editor.formats.isd import build as isd_build
from annotoolbox.editor.formats.isd import edit as isd_edit
from annotoolbox.formats import isd2070
from annotoolbox.islands import islandedits
from annotoolbox.suite.settings import CSettings


def test_the_name_the_path_and_the_sizes_the_game_form_allows():
    assert isd_blank.name(128) == "new128" and isd_blank.source(128).endswith("/normal/new128.isd")
    assert isd_blank.source(128).startswith(editorapp.ISLANDS_2070)
    assert isd_blank.size_of("new160") == 160 and isd_blank.size_of("new100") is None
    assert isd_blank.size_of("n_l03") is None and isd_blank.size_of("new") is None
    assert isd_blank.SIZES[0] == 64 and isd_blank.SIZES[-1] == 256
    assert set(editorapp.NEW_SIZES) <= set(isd_blank.SIZES)
    edits = isd_blank.new_edits(128, seed=3)
    assert edits["source"] == isd_blank.source(128) and edits["new"] == {"size": 128, "seed": 3}
    assert edits["objects"] == {} and edits["heights"] == {}
    with pytest.raises(ValueError):
        isd_blank.new_edits(100)


def test_a_new_island_is_the_blank_island_of_the_game_form_and_the_same_every_time():
    spec = {"size": 64, "seed": 2}
    data = isd_blank.island_bytes(spec)
    assert data == isd_blank.island_bytes(spec)  # the editor and the build see one island
    isl = isd2070.CIsland(data)
    assert isl.m_width == 64 and isl.m_height == 64 and isl.heightmap()[32][32] > 0.5 and isl.heightmap()[0][0] < 0


def test_the_tree_adds_new_islands_and_leaves_the_games_own_alone():
    tree = FakeTree({"data/levels/islands/normal/real.isd": b"real"})
    paths = {isd_blank.source(64): {"size": 64}}
    wrapped = isd_blank.CNewIslandTree(tree, paths)
    assert wrapped.exists(isd_blank.source(64).upper()) and wrapped.exists("data/levels/islands/normal/real.isd")
    assert not wrapped.exists("data/levels/islands/normal/other.isd")
    assert wrapped.read_bytes("data/levels/islands/normal/real.isd") == b"real"
    assert isd2070.CIsland(wrapped.read_bytes(isd_blank.source(64))).m_width == 64
    assert wrapped.archive == tree.archive  # everything else is asked of the game's tree


def test_the_edit_folder_decides_which_new_islands_exist(tmp_path):
    tree = FakeTree()
    assert isd_blank.with_new_islands(tree, tmp_path, "2070_") is tree  # none: the tree itself
    islandedits.save(tmp_path / "2070_new64.json", isd_blank.new_edits(64))
    islandedits.save(tmp_path / "1404_x.json", isd_blank.new_edits(96))  # another game's file is not read
    islandedits.save(tmp_path / "2070_isle.json", {"source": "data/levels/islands/normal/isle.isd"})
    wrapped = isd_blank.with_new_islands(tree, tmp_path, "2070_")
    assert wrapped.exists(isd_blank.source(64)) and not wrapped.exists(isd_blank.source(96))


def test_the_build_writes_the_new_island_with_its_edits_next_to_an_island_of_the_game(tmp_path):
    islandedits.save(tmp_path / "2070_new64.json", {**isd_blank.new_edits(64), "heights": {"32,32": 2.0}})
    logged = []
    files, written = isd_build.edited_files(install(), tmp_path, {}, logged.append)
    path = isd_blank.source(64)
    assert written == [path] and set(files) == {path}  # no far-view picture: the game has none to redraw
    built = isd2070.CIsland(files[path])
    plain = isd2070.CIsland(isd_blank.island_bytes({"size": 64, "seed": 1}))
    assert built.heightmap()[32][32] == pytest.approx(plain.heightmap()[32][32] + 2.0, abs=0.01)
    assert not any("no source island" in line for line in logged)


def test_the_app_opens_a_new_2070_island_makes_its_edit_file_once_and_keeps_the_mappers_work(tmp_path, monkeypatch):
    game = install()
    settings = CSettings(anno2070=str(tmp_path), workspace=str(tmp_path / "ws"))
    settings.save(tmp_path / "settings.json")
    app = editorapp.CApp(tmp_path / "settings.json")
    monkeypatch.setattr(editorapp.CApp, "tree2070", lambda self: game)
    monkeypatch.setattr(editorapp.CApp, "islands2070", lambda self: ({}, {}))
    info = app.open_blank2070(64)
    assert info["kind"] == "anno2070" and info["name"] == "new64" and info["buildable"]
    path = app.edits_dir / isd_edit.edit_name(isd_blank.source(64))
    assert islandedits.load(path)["new"] == {"size": 64, "seed": 1}
    island = app.m_editor.island()
    assert island["mode"] == "2070" and island["width"] == 65
    edits = islandedits.load(path)
    edits["heights"] = {"1,1": 0.5}  # the mapper's own work
    islandedits.save(path, edits)
    app.open_blank2070(64)
    assert islandedits.load(path)["heights"] == {"1,1": 0.5} and islandedits.load(path)["new"]["size"] == 64
    app.open("anno2070", "new64")  # the Edited list opens it by its name
    assert app.m_editor.m_source == isd_blank.source(64)
