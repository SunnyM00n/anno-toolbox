"""``sources.gametree``: a folder of loose files as a game's tree, a mod folder laid over it, where a file comes from."""

from __future__ import annotations

import pytest

from annotoolbox.sources import gametree


def _files(root, files: dict[str, bytes]):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def test_a_loose_folder_reads_by_lower_case_keys_and_says_where_a_file_is(tmp_path):
    tree = gametree.loose_tree(_files(tmp_path / "game", {"Data/A.cfg": b"a", "data/b/c.dds": b"c"}), "game")
    assert tree.paths == ["data/a.cfg", "data/b/c.dds"]
    assert tree.read("DATA\\A.cfg") == b"a" and tree.origin("data/a.cfg") == "game"
    assert tree.under("data/b/") == ["data/b/c.dds"] and tree.origin("nope") is None
    with pytest.raises(FileNotFoundError):
        tree.read("data/nope")


def test_a_mod_folder_over_the_game_wins_and_adds(tmp_path):
    game = gametree.loose_tree(_files(tmp_path / "game", {"data/a.cfg": b"game", "data/b.cfg": b"b"}), "game")
    both = game.over(_files(tmp_path / "mod", {"data/a.cfg": b"mod", "data/new.cfg": b"n"}), "my mod")
    assert both.read("data/a.cfg") == b"mod" and both.origin("data/a.cfg") == "my mod"
    assert both.read("data/b.cfg") == b"b" and both.origin("data/b.cfg") == "game"
    assert both.paths == ["data/a.cfg", "data/b.cfg", "data/new.cfg"]


def test_a_game_folder_without_its_archives_is_read_as_loose_files(tmp_path):
    tree = gametree.open_game("anno117", _files(tmp_path / "117", {"data/x.xml": b"<a/>"}))
    assert tree.game == "anno117" and tree.read("data/x.xml") == b"<a/>"
    with pytest.raises(ValueError):
        gametree.open_game("anno9999", tmp_path)


def test_every_folder_that_carries_a_path_is_named_the_winner_first(tmp_path):
    game = gametree.loose_tree(_files(tmp_path / "game", {"data/a.cfg": b"game", "data/b.cfg": b"b"}), "game")
    both = game.over(_files(tmp_path / "mod", {"data/a.cfg": b"mod"}), "my mod")
    assert both.origins("data/a.cfg") == ["my mod", "game"] and both.origins("data/b.cfg") == ["game"]
    assert both.origins("data/none.cfg") == [] and both.deleted == []


def test_a_loose_tree_names_the_real_file_and_a_mod_folder_over_it_too(tmp_path):
    game = gametree.loose_tree(_files(tmp_path / "game", {"Data/A.cfg": b"a", "data/b.cfg": b"b"}), "game")
    both = game.over(_files(tmp_path / "mod", {"data/b.cfg": b"mod"}), "my mod")
    assert game.local("DATA/a.cfg") == tmp_path / "game" / "Data" / "A.cfg" and game.local("data/none") is None
    assert both.local("data/b.cfg") == tmp_path / "mod" / "data" / "b.cfg" and both.local("data/a.cfg") is None


def test_the_stamp_of_a_game_changes_with_its_files_and_opens_nothing(tmp_path):
    root = _files(tmp_path / "117", {"data/x.xml": b"<a/>"})
    before = gametree.game_stamp("anno117", root)
    assert before == gametree.game_stamp("anno117", root) == gametree.open_game("anno117", root).stamp
    (root / "data" / "x.xml").write_bytes(b"<a>more</a>")
    assert gametree.game_stamp("anno117", root) != before
    with pytest.raises(ValueError):
        gametree.game_stamp("anno9999", root)
