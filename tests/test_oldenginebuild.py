"""1404 and 1701: edited islands built into the archives the games load over the original, installed and restored."""

import json
from pathlib import Path

import pytest

from annotoolbox.editor import archivedelivery, oldenginebuild
from annotoolbox.editor.formats.isd import build as isd_build
from annotoolbox.formats import isd2070, rda
from annotoolbox.othergames import installs, isd1701
from annotoolbox.sources.overlay import SourceTree
from annotoolbox.suite.settings import Settings
from test_isd1701 import isd_bytes as isd1701_bytes
from test_island2070edit import isd_bytes as isd2070_bytes

ISLE = "data/levels/islands/normal/isle.isd"
VENICE = "addondata/levels/islands/north/venice.isd"
LOG = [].append


def _save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def install_1404(root: Path) -> Path:
    _save(root / "Anno1404.exe", b"")
    _save(root / "maindata/data0.rda", rda.write_v20({ISLE: isd2070_bytes(), "data/a.txt": b"a"}, 100))
    _save(root / "maindata/data1.rda", rda.write_v20({"data/b.txt": b"b"}, 100))
    _save(root / "maindata/data2.rda", rda.write_v20({"data/system/shader.fx": b"fx"}, 100))
    _save(root / "addon/data0.rda", rda.write_v20({VENICE: isd2070_bytes()}, 100))
    _save(root / "addon/data1.rda", rda.write_v20({"addondata/c.txt": b"c"}, 100))
    return root


def built(key: str, game: Path, edits_dir: Path) -> tuple[dict[str, bytes], list[str], Settings]:
    """The archives the editor's build makes for a game (``IslandDelivery``: edited, then package), what it wrote
    and the settings (their workspace holds the backups)."""
    settings = Settings(**{key: str(game)}, workspace=str(edits_dir.parent / "ws"))
    d = archivedelivery.ISLAND_DELIVERIES[key]
    tree = d.tree(settings)
    files, written = d.edited(tree, edits_dir, LOG)
    return d.package(files, written, settings, LOG, tree), written, settings


def edits(edits_dir: Path, prefix: str, source: str, **more) -> None:
    edits_dir.mkdir(exist_ok=True)
    name = f"{prefix}{Path(source).stem}.json"
    (edits_dir / name).write_text(json.dumps({"source": source, **more}), encoding="utf-8")


def entries(path: Path) -> dict[str, bytes]:
    arc = rda.RdaArchive(path)
    return {e.path: arc.read_entry(e) for e in arc}


def test_1404_islands_go_into_the_last_data_archive_of_their_folder(tmp_path: Path):
    game = install_1404(tmp_path / "game")
    objects = {"objects": {"2070:12": {"position": [3.0, 1.0, 4.0]}}}
    edits(tmp_path / "e", "1404_", ISLE, **objects)
    edits(tmp_path / "e", "1404_", VENICE, **objects)
    archives, written, _settings = built("anno1404", game, tmp_path / "e")
    assert sorted(archives) == ["addon/data1.rda", "maindata/data2.rda"] and sorted(written) == [VENICE, ISLE]
    (tmp_path / "out").mkdir()
    for rel, data in archives.items():
        _save(tmp_path / "out" / rel, data)
    main = entries(tmp_path / "out/maindata/data2.rda")
    assert main["data/system/shader.fx"] == b"fx" and oldenginebuild.MARKER in main  # the archive's own files stay
    moved = next(o for o in isd2070.Island(main[ISLE]).objects() if o.id == 12)
    assert (moved.x, moved.y, moved.z) == (3.0, 1.0, 4.0)
    assert VENICE not in main and VENICE in entries(tmp_path / "out/addon/data1.rda")


def test_install_keeps_the_games_archive_and_restore_puts_it_back(tmp_path: Path):
    game = install_1404(tmp_path / "game")
    edits(tmp_path / "e", "1404_", ISLE, objects={"2070:12": {"deleted": True}})
    original = (game / "maindata/data2.rda").read_bytes()
    archives, _, settings = built("anno1404", game, tmp_path / "e")
    backup = archivedelivery.old_engine_backups(settings, "anno1404")
    oldenginebuild.install_archives("anno1404", game, archives, backup, LOG)
    assert isd_build.is_ours(game / "maindata/data2.rda") and not isd_build.is_ours(game / "maindata/data1.rda")
    assert (backup / "anno1404-maindata-data2.rda").read_bytes() == original
    # a second build starts from the game's own island, not from the one the first install wrote
    replace = oldenginebuild.replacement("anno1404", game, backup)
    tree = installs.GAMES["anno1404"].tree(game, replace)
    assert len(isd2070.Island(tree.read_bytes(ISLE)).objects()) == 4
    again, _, _settings = built("anno1404", game, tmp_path / "e")
    oldenginebuild.install_archives("anno1404", game, again, backup, LOG)
    assert (backup / "anno1404-maindata-data2.rda").read_bytes() == original  # still the game's
    assert oldenginebuild.restore("anno1404", game, backup, LOG)
    assert (game / "maindata/data2.rda").read_bytes() == original
    assert not oldenginebuild.restore("anno1404", game, backup, LOG)


def test_1404_archive_of_ours_without_the_games_copy_refuses_build_and_install(tmp_path: Path):
    """Its copy used to live in the workspace: after a workspace change the build packed an archive without the
    game's own files, and the install wrote it, so the game lost them."""
    game = install_1404(tmp_path / "game")
    edits(tmp_path / "e", "1404_", ISLE, objects={"2070:12": {"deleted": True}})
    archives, _, settings = built("anno1404", game, tmp_path / "e")
    backup = archivedelivery.old_engine_backups(settings, "anno1404")
    oldenginebuild.install_archives("anno1404", game, archives, backup, LOG)
    (backup / "anno1404-maindata-data2.rda").unlink()
    ours = (game / "maindata/data2.rda").read_bytes()
    with pytest.raises(RuntimeError, match="verify the files in the launcher"):
        built("anno1404", game, tmp_path / "e")
    with pytest.raises(RuntimeError, match="verify the files in the launcher"):
        oldenginebuild.install_archives("anno1404", game, archives, backup, LOG)
    assert (game / "maindata/data2.rda").read_bytes() == ours  # nothing was written


def test_1404_copies_kept_in_the_workspace_move_out_of_it_and_a_repaired_archive_is_kept_afresh(tmp_path: Path):
    game = install_1404(tmp_path / "game")
    edits(tmp_path / "e", "1404_", ISLE, objects={"2070:12": {"deleted": True}})
    archives, _, settings = built("anno1404", game, tmp_path / "e")
    original = (game / "maindata/data2.rda").read_bytes()
    old = Path(settings.workspace) / "old-engine-originals" / "anno1404-maindata-data2.rda"
    _save(old, original)  # where an earlier version kept it
    kept = archivedelivery.old_engine_backups(settings, "anno1404") / old.name
    assert kept.read_bytes() == original and not old.exists()
    assert Path(settings.workspace) not in kept.parents  # a workspace change cannot orphan it
    oldenginebuild.install_archives("anno1404", game, archives, kept.parent, LOG)
    repaired = rda.write_v20({"data/system/shader.fx": b"fx2"}, 200)
    _save(game / "maindata/data2.rda", repaired)  # the launcher's file check (or an update) put the game's back
    again, _, _settings = built("anno1404", game, tmp_path / "e")
    assert entries_of(again["maindata/data2.rda"])["data/system/shader.fx"] == b"fx2"
    oldenginebuild.install_archives("anno1404", game, again, kept.parent, LOG)
    assert kept.read_bytes() == repaired


def entries_of(data: bytes) -> dict[str, bytes]:
    arc = rda.RdaArchive.from_bytes(data)
    return {e.path: arc.read_entry(e) for e in arc}


def test_1404_edit_of_an_island_no_data_archive_holds_is_refused(tmp_path: Path):
    game = install_1404(tmp_path / "game")
    _save(game / "ahe/a4he0.rda", rda.write_v22({"data/levels/islands/normal/ahe.isd": isd2070_bytes()}))
    edits(tmp_path / "e", "1404_", "data/levels/islands/normal/ahe.isd")
    tree = SourceTree.from_archives([game / "maindata/data0.rda", game / "ahe/a4he0.rda"])
    with pytest.raises(ValueError):
        files, written = oldenginebuild.edited_1404(tree, tmp_path / "e", LOG)
        oldenginebuild.archives_1404(tree, game, files, written, lambda f: f, LOG)


def install_1701(root: Path) -> Path:
    _save(root / "Anno1701.exe", b"")
    isle = "data/levels/islands/north/isle.isd"
    _save(root / "data0.rda", rda.write_v11({isle: isd1701_bytes()}, 1000))
    _save(root / "data1.rda", rda.write_v11({"data/a.txt": b"a"}, 1000))
    _save(root / "data2_eng.rda", rda.write_v11({"data/b.txt": b"b"}, 1000))
    return root


def test_1701_islands_go_into_a_new_archive_whose_files_are_newer(tmp_path: Path):
    game = install_1701(tmp_path / "game")
    source = "data/levels/islands/north/isle.isd"
    edits(tmp_path / "e", "1701_", source, objects={"anno1701:1": {"position": [1.0, 0.0, 1.0]}})
    archives, written, settings = built("anno1701", game, tmp_path / "e")
    backup = archivedelivery.old_engine_backups(settings, "anno1701")
    assert list(archives) == ["data3.rda"] and written == [source]  # the first number the game does not mount
    oldenginebuild.install_archives("anno1701", game, archives, backup, LOG)
    assert [p.name for p in isd1701.archives(game)][-1] == "data3.rda"
    after = isd1701.source_tree(game)  # the game's rule: the newer copy wins
    old = isd1701.Island1701(isd1701_bytes()).objects()[1]
    new = isd1701.Island1701(after.read_bytes(source)).objects()[1]
    assert (old.x, new.x) == (2.0, 1.0)
    # our archive is left out of the tree a build starts from, and a second build reuses its name
    replace = oldenginebuild.replacement("anno1701", game, backup)
    clean = installs.GAMES["anno1701"].tree(game, replace)
    assert isd1701.Island1701(clean.read_bytes(source)).objects()[1].x == 2.0
    again, _, _settings = built("anno1701", game, tmp_path / "e")
    assert list(again) == ["data3.rda"]
    assert oldenginebuild.restore("anno1701", game, backup, LOG) and not (game / "data3.rda").exists()
