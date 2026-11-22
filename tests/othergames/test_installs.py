from pathlib import Path

import pytest

from annotoolbox.editor import editorapp
from annotoolbox.formats import rda
from annotoolbox.othergames import installs
from annotoolbox.sources.overlay import CSourceTree
from annotoolbox.suite.settings import CSettings


def test_archives_go_in_number_order(tmp_path: Path):
    for n in (0, 1, 2, 10, 11):
        (tmp_path / f"data{n}.rda").write_bytes(b"")
    assert [p.stem for p in installs._numbered(tmp_path)] == ["data0", "data1", "data2", "data10", "data11"]


def test_an_install_is_told_by_its_executable_and_its_engine(tmp_path: Path):
    (tmp_path / "Anno 1404").mkdir()
    (tmp_path / "Anno 1404" / "Anno1404Addon.exe").write_bytes(b"")
    (tmp_path / "Anno 1800" / "Bin" / "Win64").mkdir(parents=True)
    (tmp_path / "Anno 1800" / "Bin" / "Win64" / "Anno1800.exe").write_bytes(b"")
    found = installs.detect(list(tmp_path.iterdir()))
    assert found["anno1404"].endswith("Anno 1404") and found["anno1800"].endswith("Anno 1800") and not found["anno2205"]
    assert installs.GAMES["anno1404"].family == installs.OLD and installs.GAMES["anno1800"].family == installs.NEW
    assert [installs.GAMES[k].engine for k in ("anno1701", "anno1404", "anno2205", "anno1800")] == [3, 4, 6, 7]


def test_a_listed_tree_lets_later_archives_win(tmp_path: Path):
    (tmp_path / "a.rda").write_bytes(rda.write_v20({"data/x.txt": b"old", "data/y.txt": b"only"}))
    (tmp_path / "b.rda").write_bytes(rda.write_v22({"data/x.txt": b"new"}))
    tree = CSourceTree.from_archives([tmp_path / "a.rda", tmp_path / "missing.rda", tmp_path / "b.rda"], tmp_path / "c")
    assert tree.read_bytes("data/x.txt") == b"new" and tree.read_bytes("data/y.txt") == b"only"
    game = installs.SGame("t", "Test", 4, (), lambda root: [], ("data/",), ".txt", None)
    assert installs.islands(game, tree) == ["data/x.txt", "data/y.txt"]


def test_the_app_lists_the_other_games(tmp_path: Path):
    settings = CSettings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods"))
    settings.anno1404 = str(tmp_path / "nothing here")
    path = tmp_path / "settings.json"
    settings.save(path)
    app = editorapp.CApp(path)
    listed = app.other_games()
    assert (
        set(listed) == set(installs.GAMES) - set(editorapp.NATIVE)
        and listed["anno1404"]["problem"]
        and listed["anno1404"]["engine"] == 4
    )


def test_every_readable_game_has_its_viewer():
    assert {g.editor for g in installs.GAMES.values() if g.editor} - {"117", "2070"} <= set(editorapp.VIEWER_CLASSES)
    assert installs.GAMES["anno1800"].editor == "a7m" and installs.GAMES["anno1404"].editor == "isd"
    assert all(g.editor for g in installs.GAMES.values())  # every game opens, read-only


def test_117_and_2070_are_records_like_the_others(tmp_path: Path):
    (tmp_path / "70" / "maindata").mkdir(parents=True)
    (tmp_path / "70" / "Anno5.exe").write_bytes(b"")
    (tmp_path / "7a" / "maindata").mkdir(parents=True)
    (tmp_path / "7a" / "maindata" / "data0.rda").write_bytes(b"")  # 2205 has this archive too
    (tmp_path / "117" / "Bin" / "Win64").mkdir(parents=True)
    (tmp_path / "117" / "Bin" / "Win64" / "Anno117.exe").write_bytes(b"")
    assert not installs.GAMES["anno2070"].is_install(tmp_path / "70") and not installs.GAMES["anno2070"].is_install(
        tmp_path / "7a"
    )
    (tmp_path / "70" / "maindata" / "data0.rda").write_bytes(b"")
    found = installs.detect(sorted(tmp_path.iterdir()))
    assert found["anno2070"] == str(tmp_path / "70") and found["anno117"] == str(tmp_path / "117")
    assert list(installs.GAMES)[:2] == ["anno117", "anno1800"] and installs.GAMES["anno2070"].engine == 5
    assert installs.GAMES["anno117"].edit_prefix == "117_" and installs.GAMES["anno2070"].edit_prefix == "2070_"


def test_the_game_table_gives_the_documents_folders_and_executables(tmp_path: Path, monkeypatch):
    from annotoolbox.sources import gametree

    monkeypatch.setattr(gametree, "documents_dir", lambda: tmp_path)
    assert gametree.user_dir("anno117") == tmp_path / "Anno 117 - Pax Romana"
    assert gametree.user_dir("anno1800") == tmp_path / "Anno 1800"
    assert gametree.EXES["anno117"] == "Bin/Win64/Anno117.exe" and gametree.EXES["anno1800"] == "Bin/Win64/Anno1800.exe"
    with pytest.raises(ValueError):
        gametree.user_dir("anno2070")


def test_2070_opens_through_its_record_like_every_game(tmp_path: Path):
    from annotoolbox.sources import gametree

    (tmp_path / "maindata").mkdir()
    (tmp_path / "Anno5.exe").write_bytes(b"")
    (tmp_path / "maindata" / "data0.rda").write_bytes(rda.write_v20({"data/levels/islands/a.isd": b"island"}))
    tree = gametree.open_game("anno2070", tmp_path, tmp_path / "cache")
    assert (
        tree.read("data/levels/islands/a.isd") == b"island" and tree.origin("data/levels/islands/a.isd") == "data0.rda"
    )
    assert tree.m_folder == tmp_path and tree.m_stamp == gametree.game_stamp("anno2070", tmp_path)


def test_the_capability_flags_pick_the_games_in_table_order():
    assert installs.keys(lambda g: g.mod_loader) == ("anno117", "anno1800")
    assert installs.keys(lambda g: not g.mod_loader) == ("anno2205", "anno2070", "anno1404", "anno1701")
    assert installs.keys(lambda g: g.granny) == ("anno1404", "anno1701")
    assert installs.keys(lambda g: g.family == installs.NEW) == ("anno117", "anno1800", "anno2205")
