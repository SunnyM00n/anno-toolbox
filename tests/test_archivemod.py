"""The older games' archive mods as projects (suite/archivemod.py, archiverecord.py, archivemodbuild.py): the import
sorts the members, keeps the layout, rebuilds the archive; the build writes the same archive when nothing was edited."""

from __future__ import annotations

import os
import zlib
from pathlib import Path

import pytest

from annotoolbox.formats import rda, rdalayout
from annotoolbox.suite import archivemod, archivemodbuild, archiverecord, carriedpart, project
from annotoolbox.suite.settings import Settings

TEXT = b"<Asset><Values><Standard><GUID>1</GUID></Standard></Values></Asset>\n" * 30
FILES = {
    "data/config/export/assets.xml": TEXT,
    "data/graphics/buildings/farm/farm.cfg": b"<Config>farm</Config>" * 25,
    "data/graphics/buildings/farm/farm.rdm": bytes(range(256)) * 4,
    "data/graphics/ui/icon.png": b"\x89PNG" + bytes(300),
    "data/sound/music.bnk": b"BKHD" + bytes(200),
}
STAMPS = dict(zip(FILES, range(1_300_000_000, 1_300_000_005), strict=True))


def _foreign(data: bytes, level: int = -1) -> bytes:
    """Compressed as another program might (not the writer's zlib level 1)."""
    c = zlib.compressobj(9, zlib.DEFLATED, 15, 1, zlib.Z_FILTERED)
    return c.compress(data) + c.flush()


def make_archive(dest: Path, monkeypatch, foreign: bool = True) -> Path:
    """A 2070-style archive: three blocks of flags 3, a deleted block, an empty block; compressed by ``_foreign``."""
    paths = list(FILES)
    with monkeypatch.context() as m:
        if foreign:
            m.setattr(rda.zlib, "compress", _foreign)
        with rda.RdaWriter(dest, rda.V2_0, STAMPS, dedup=False) as w:
            w.add_block([(p, FILES[p]) for p in paths[:2]], 3)
            w.add_block([(p, FILES[p]) for p in paths[2:4]], 3)
            w.add_block([(paths[4], FILES[paths[4]])], 2)
            w.add_deleted(["data/old/gone.cfg"])
            w.add_empty(3)
    assert foreign is False or _foreign(TEXT) != zlib.compress(TEXT, 1)
    return dest


def do_import(tmp_path, source: Path, game: str | None = "anno2070", **kwargs) -> archivemod.ArchiveReport:
    return archivemod.import_archive_mod(source, tmp_path / "proj", game, **kwargs)


def build(proj: project.Project, log=lambda _t: None) -> project.BuildResult:
    parts = [carriedpart.PART]
    for key, edits in (("objects", "object-edits"), ("sound", "sound-edits")):
        parts.append(project.EditsFolder(edits).part(key, key.title(), key))
    return project.build(proj, parts, Settings(), log, archivemodbuild.deliveries())


def test_an_archive_is_imported_sorted_and_comes_back_byte_for_byte(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "farm-mod.rda", monkeypatch)
    before = source.read_bytes()
    report = do_import(tmp_path, source)
    folder = tmp_path / "proj"
    assert report.identical is True and report.differences == [] and report.game == "anno2070"
    assert report.files == 5 and report.format == "2.0" and report.blocks == 5 and report.deleted == 1
    assert (folder / "object-edits/anno2070/data/graphics/buildings/farm/farm.cfg").read_bytes() == FILES[
        "data/graphics/buildings/farm/farm.cfg"
    ]
    assert (folder / "sound-edits/anno2070/data/sound/music.bnk").is_file()
    assert (folder / "mod-files/anno2070/data/graphics/buildings/farm/farm.rdm").is_file()
    assert (folder / "mod-files/anno2070/data/config/export/assets.xml").is_file()
    assert report.taken == {"objects": 1, "sound": 1} and "carried" not in report.lines()[0]
    assert source.read_bytes() == before  # the archive is never changed
    assert any("byte for byte" in line for line in report.lines())
    proj = project.Project.open(folder)
    assert proj.game == "anno2070" and proj.mod_id == "farm-mod"
    result = build(proj)
    built = proj.build_dir / "farm-mod.rda"
    assert built.read_bytes() == before and result.parts and not (proj.build_dir / "modinfo.json").exists()
    assert not (folder / archivemod.CHECK_FILE).exists() and not (folder / archivemodbuild.SCRATCH).exists()
    diff = archivemodbuild.compare_members(source, built)
    assert diff.identical and diff.same == 5 and archivemodbuild.same_bytes(source, built)


def test_an_edit_changes_only_its_member_and_keeps_the_rest_as_it_was_stored(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "farm-mod.rda", monkeypatch)
    do_import(tmp_path, source)
    proj = project.Project.open(tmp_path / "proj")
    cfg = tmp_path / "proj/object-edits/anno2070/data/graphics/buildings/farm/farm.cfg"
    cfg.unlink()  # an edit is a new file (the Toolbox never writes in place)
    cfg.write_bytes(b"<Config>edited</Config>")
    extra = tmp_path / "proj/object-edits/anno2070/data/graphics/buildings/new.cfg"
    extra.write_bytes(b"<Config>new</Config>" * 10)
    build(proj)
    old, new = rda.RdaArchive(source), rda.RdaArchive(proj.build_dir / "farm-mod.rda")
    assert new.read("data/graphics/buildings/farm/farm.cfg") == b"<Config>edited</Config>"
    assert new.read("data/graphics/buildings/new.cfg") == b"<Config>new</Config>" * 10
    assert new.deleted == old.deleted
    for path in FILES:
        if path != "data/graphics/buildings/farm/farm.cfg":
            assert new.read(path) == old.read(path)
            assert new.stored_bytes(new.entries[path]) == old.stored_bytes(old.entries[path])  # not compressed again
            assert new.entries[path].mtime == old.entries[path].mtime
    assert (
        new.entries["data/graphics/buildings/farm/farm.cfg"].mtime
        > old.entries["data/graphics/buildings/farm/farm.cfg"].mtime
    )
    assert list(new.entries)[-1] == "data/graphics/buildings/new.cfg"  # a member the layout lacks: a block at the end


def test_without_the_original_the_members_are_compressed_again_and_the_contents_stay(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "farm-mod.rda", monkeypatch)
    do_import(tmp_path, source)
    proj = project.Project.open(tmp_path / "proj")
    archiverecord.original_path(proj.folder, archiverecord.read_record(proj.folder)).unlink()
    lines: list[str] = []
    build(proj, lines.append)
    assert any("not in the project any more" in line for line in lines)
    diff = archivemodbuild.compare_members(source, proj.build_dir / "farm-mod.rda")
    assert diff.identical and diff.same == 5
    built = proj.build_dir / "farm-mod.rda"
    assert not archivemodbuild.same_bytes(source, built)  # the foreign compression is not the writer's
    assert rdalayout.differences(
        rdalayout.read_layout(rda.RdaArchive(source)), rdalayout.read_layout(rda.RdaArchive(built))
    ) == [rdalayout.COMPRESSION]


def test_what_a_rebuild_cannot_keep_is_named(tmp_path):
    source = tmp_path / "caps.rda"
    rda.write_v11_file(source, [("Data/A.cfg", b"one" * 30), ("data/a.cfg", b"two" * 30), ("data/b.cfg", b"b" * 30)])
    report = do_import(tmp_path, source, game=None)  # V1.1 is Anno 1701's alone
    assert report.game == "anno1701" and report.identical is False and "files" in report.differences
    assert report.left_out == [("data/a.cfg", "reason_twice")]
    assert (tmp_path / "proj/object-edits/anno1701/data/a.cfg").read_bytes() == b"one" * 30  # lower-cased, first wins
    text = "\n".join(report.lines())
    assert "Left out" in text and "data/a.cfg" in text
    assert archiverecord.read_record(tmp_path / "proj").renamed == {"data/a.cfg": "Data/A.cfg"}


@pytest.mark.parametrize("compress", [True, False])
def test_a_1701_archive_round_trips(tmp_path, compress):
    source = tmp_path / "data6.rda"
    rda.write_v11_file(source, list(FILES.items()), STAMPS, compress=compress)
    report = do_import(tmp_path, source, game="anno1701")
    assert report.identical is True and report.game == "anno1701"
    proj = project.Project.open(tmp_path / "proj")
    build(proj)
    assert (proj.build_dir / "data6.rda").read_bytes() == source.read_bytes()


def test_a_resident_block_and_shared_copies_come_back_too(tmp_path):
    source = tmp_path / "res.rda"
    with rda.RdaWriter(source, rda.V2_0, 7, dedup=True, allow_resident=True) as w:
        w.add_block([("data/a/one.cfg", b"x" * 90), ("data/a/two.cfg", b"y" * 70)], 7)
        w.add_block([("data/b/same1.cfg", b"z" * 50), ("data/b/same2.cfg", b"z" * 50)], 3)
    report = do_import(tmp_path, source, game="anno1404")
    assert report.identical is True
    proj = project.Project.open(tmp_path / "proj")
    build(proj)
    assert (proj.build_dir / "res.rda").read_bytes() == source.read_bytes()


def test_a_folder_of_files_is_built_into_an_archive_of_the_games_layout(tmp_path):
    folder = tmp_path / "loose"
    for rel, data in FILES.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(data)
    report = do_import(tmp_path, folder, game="anno2070")
    assert report.identical is None and report.archive == "loose.rda" and report.format == "2.0"
    assert any("loose files" in line for line in report.lines())
    proj = project.Project.open(tmp_path / "proj")
    build(proj)
    built = rda.RdaArchive(proj.build_dir / "loose.rda")
    assert built.version is rda.V2_0 and all(f == 3 for f, _n in rdalayout.read_layout(built).blocks)
    assert archivemodbuild.compare_members(folder, proj.build_dir / "loose.rda").identical
    with pytest.raises(archivemod.ImportRefused):
        archivemod.import_archive_mod(folder, tmp_path / "other", None)  # a folder says nothing about its game


def test_an_import_is_refused_with_a_sentence_and_leaves_nothing(tmp_path, monkeypatch):
    v20 = make_archive(tmp_path / "a.rda", monkeypatch, foreign=False)
    with pytest.raises(archivemod.ImportRefused, match="2.0"):
        do_import(tmp_path, v20, game=None)  # 2070 and 1404 both read V2.0
    with pytest.raises(archivemod.ImportRefused, match="does not read"):
        do_import(tmp_path, v20, game="anno1701")
    with pytest.raises(archivemod.ImportRefused):
        do_import(tmp_path, v20, game="anno9999")
    junk = tmp_path / "junk.rda"
    junk.write_bytes(b"not an archive" * 100)
    with pytest.raises(archivemod.ImportRefused):
        do_import(tmp_path, junk)
    with pytest.raises(archivemod.ImportRefused):
        do_import(tmp_path, tmp_path / "missing.rda")
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "x").write_text("x")
    with pytest.raises(archivemod.ImportRefused):
        archivemod.import_archive_mod(v20, taken, "anno2070")
    assert not (tmp_path / "proj").exists() and sorted(p.name for p in taken.iterdir()) == ["x"]


def test_stopping_the_import_removes_the_project_and_keeps_the_archive(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "a.rda", monkeypatch, foreign=False)
    before = source.read_bytes()
    seen = []

    def say(text: str) -> None:
        seen.append(text)
        if len(seen) == 3:  # after the members are written
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        do_import(tmp_path, source, say=say)
    assert not (tmp_path / "proj").exists() and source.read_bytes() == before


def test_the_game_is_found_from_the_archives_version(tmp_path, monkeypatch):
    v20 = make_archive(tmp_path / "a.rda", monkeypatch, foreign=False)
    assert archivemod.guess_game(v20) == ["anno2070", "anno1404"]
    one = tmp_path / "b.rda"
    rda.write_v11_file(one, [("data/x.cfg", b"x" * 40)])
    assert archivemod.guess_game(one) == ["anno1701"]
    assert archivemod.guess_game(tmp_path / "nothing") == []
    assert archivemod.guess_game(tmp_path) == []


def test_installing_an_archive_mod_is_left_to_the_person_and_other_projects_go_to_the_island_delivery(
    tmp_path, monkeypatch
):
    source = make_archive(tmp_path / "a.rda", monkeypatch, foreign=False)
    do_import(tmp_path, source)
    proj = project.Project.open(tmp_path / "proj")
    deliveries = archivemodbuild.deliveries()
    with pytest.raises(RuntimeError, match="copy it"):
        project.install_built(proj, Settings(), deliveries, lambda _t: None)
    seen = []
    base = project.Delivery(lambda files, p, s, log: seen.append(files) or {"x.rda": b"x"}, lambda p, s, log: "ok")
    plain = project.Project(tmp_path / "plain", mod_id="plain", game="anno2070")
    routed = archivemodbuild.routed(base)
    assert routed.package({"a": b"1"}, plain, None, print) == {"x.rda": b"x"} and seen == [{"a": b"1"}]
    assert routed.install(plain, None, print) == "ok"
    with pytest.raises(ValueError):
        archivemodbuild.routed(None).package({}, plain, None, print)


def test_the_command_imports_builds_and_verifies_an_archive(tmp_path, monkeypatch, capsys):
    from annotoolbox.suite import importcommands

    source = make_archive(tmp_path / "farm-mod.rda", monkeypatch)
    settings = tmp_path / "settings.json"
    code = importcommands.cmd_import(
        importcommands.argparse.Namespace(
            source=source, folder=tmp_path / "proj", copy=False, no_hash=False, build=False, verify=True,
            settings=settings, game="anno2070",
        )
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and "The built archive is byte for byte" in out
    assert (tmp_path / "proj/build/farm-mod/farm-mod.rda").read_bytes() == source.read_bytes()


GAME = os.environ.get("ANNO2070_DIR")


@pytest.mark.skipif(not GAME or not Path(GAME, "maindata/Data0.rda").is_file(), reason="no Anno 2070 install")
def test_a_shipped_2070_archive_rebuilds_to_the_same_bytes(tmp_path):
    source = Path(GAME, "maindata/Data0.rda")  # read only; 145 KB, compressed by the game's own tool
    report = archivemod.import_archive_mod(source, tmp_path / "proj", "anno2070")
    assert report.identical is True and report.left_out == []


def test_the_project_window_imports_an_archive_with_its_report_and_opens_the_project(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import apps, shell
    from annotoolbox.suite.projectpanel import ProjectDialog

    app = QApplication.instance() or QApplication([])
    settings = Settings(workspace=str(tmp_path / "ws"))
    dialog = ProjectDialog(shell.SuiteWindow([], settings, tmp_path / "settings.json"), apps.parts())
    source = make_archive(tmp_path / "farm-mod.rda", monkeypatch)
    dialog.import_archive(source, "anno2070", tmp_path / "proj", wait=True)
    app.processEvents()
    log = dialog.log.toPlainText()
    assert "imported for anno2070" in log and "byte for byte the imported one" in log
    assert dialog.mod_id.text() == "farm-mod" and settings.project == str(tmp_path / "proj")
    assert dialog.stop_button.isHidden()
    dialog.import_archive(source, "anno2070", tmp_path / "proj", wait=True)  # not empty now: said, not raised
    assert "not empty" in dialog.log.toPlainText()
