"""The edge cases of importing a mod (suite/importchecks.py, importresume.py, reimport.py, projectimport.py): the
conditions report, the build stamp, the lint of the original, a duplicate in the Mods app, case clashes, a cut-short
import that resumes or is removed, and a re-import compared by manifest."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from annotoolbox.modmgr.profiles import CProfileStore
from annotoolbox.suite import (
    carriedpart,
    importchecks,
    importmeta,
    importresume,
    project,
    projectimport,
    reimport,
)
from annotoolbox.suite.settings import CSettings

ASSETS = "data/base/config/export/assets.xml"
OPS = b"""<ModOps>
  <ModOp Type="merge" GUID="1000" Condition="#friend-mod"><Asset><Values/></Asset></ModOp>
  <ModOp Type="merge" GUID="1001" Condition="'$flag' = 'true'"><Asset><Values/></Asset></ModOp>
  <ModOp Type="merge" GUID="1002" Condition="!#other-mod"><Asset><Values/></Asset></ModOp>
</ModOps>
"""


def modinfo(mod_id: str = "edge-mod", stamp: str = "", extra: dict | None = None) -> str:
    desc = "A test mod." + (f" Generated for the game build: {stamp}." if stamp else "")
    info = {
        "Anno": 8,
        "ModID": mod_id,
        "Version": "1.0.0",
        "ModName": {"English": "Edge mod"},
        "Description": {"English": desc},
        "Dependencies": {"Optional": ["friend-mod"]},
    }
    return json.dumps({**info, **(extra or {})})


def make_mod(root: Path, stamp: str = "") -> Path:
    files: dict[str, bytes | str] = {
        "modinfo.json": modinfo(stamp=stamp),
        ASSETS: OPS,
        "data/graphics/x.rdm": bytes(range(256)) * 8,
        "data/graphics/x.dds": b"DDS " * 50,
        "data/graphics/x.cfg": b"cfg",
        "data/other/a.txt": b"a",
        "data/other/b.txt": b"b",
        "data/other/c.txt": b"c",
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
    return root


def replace(path: Path, data: bytes) -> None:
    """Write ``path`` as a new file (a hard link keeps the old bytes, as the Toolbox's own writes do)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def test_conditions_are_counted_and_the_files_stay_carried_with_the_reason(tmp_path):
    mods = tmp_path / "mods"
    make_mod(mods / "friend-mod")
    (mods / "friend-mod" / "modinfo.json").write_text(modinfo("friend-mod"), encoding="utf-8")
    ctx = importchecks.CImportContext(m_mods_folders=[mods])
    report = projectimport.import_mod(make_mod(tmp_path / "src"), tmp_path / "proj", None, context=ctx)
    text = "\n".join(report.lines())
    assert "3 conditions in the patch files, 1 of them" in text
    assert "friend-mod: a dependency of the mod, installed" in text
    assert "other-mod: not a dependency of the mod, not installed here" in text
    assert "stay carried as written" in text and "#friend-mod (1 times, first in " in text


def test_a_mod_without_conditions_says_nothing_about_them(tmp_path):
    src = make_mod(tmp_path / "src")
    (src / ASSETS).write_bytes(b"<ModOps/>")
    report = projectimport.import_mod(src, tmp_path / "proj")
    assert "conditions in the patch files" not in "\n".join(report.lines())


@pytest.mark.parametrize(
    ("installed", "said"),
    [
        ("zz_patchfiles_005.rda", "is 'ignored'"),
        ("zz_patchfiles_004.rda", "was generated for the installed game build"),
        ("", "not known here"),
    ],
)
def test_a_build_stamp_is_compared_with_the_installed_build(tmp_path, installed, said):
    src = make_mod(tmp_path / "src", stamp="zz_patchfiles_004.rda")
    ctx = importchecks.CImportContext(m_build=installed)
    text = "\n".join(projectimport.import_mod(src, tmp_path / "proj", None, context=ctx).lines())
    if installed == "zz_patchfiles_005.rda":
        assert (
            "generated for the game build zz_patchfiles_004.rda; the installed build is zz_patchfiles_005.rda" in text
        )
    else:
        assert said in text


def test_the_loaders_lint_of_the_original_is_in_the_report(tmp_path):
    ctx = importchecks.CImportContext(m_has_base=lambda _path: False)
    text = "\n".join(projectimport.import_mod(make_mod(tmp_path / "src"), tmp_path / "proj", None, context=ctx).lines())
    assert "Mod loader lint of the original:" in text and "no game file" in text
    plain = "\n".join(projectimport.import_mod(make_mod(tmp_path / "s2"), tmp_path / "p2").lines())
    assert "Patch targets were not checked" in plain


def test_a_mod_id_the_mods_app_holds_already_is_reported_with_the_profile(tmp_path):
    mods = tmp_path / "mods"
    make_mod(mods / "copy-of-it")
    (mods / "copy-of-it" / "modinfo.json").write_text(modinfo("edge-mod"), encoding="utf-8")
    store = CProfileStore(mods)
    store.create("main", enabled_ids=["edge-mod"])
    store.set_active("main")
    ctx = importchecks.CImportContext(m_mods_folders=[mods], m_profile=store.read("main"))
    text = "\n".join(projectimport.import_mod(make_mod(tmp_path / "src"), tmp_path / "proj", None, context=ctx).lines())
    assert "already holds a mod edge-mod (1.0.0)" in text and "copy-of-it" in text
    assert "active profile lists edge-mod, switched on" in text


def test_a_mod_imported_from_the_mods_folder_is_not_its_own_duplicate(tmp_path):
    mods = tmp_path / "mods"
    src = make_mod(mods / "edge-mod")
    ctx = importchecks.CImportContext(m_mods_folders=[mods])
    text = "\n".join(projectimport.import_mod(src, tmp_path / "proj", None, context=ctx).lines())
    assert "already holds" not in text


def test_paths_that_differ_only_in_case_are_found_and_the_later_is_left_out_where_the_disk_cannot_hold_both(tmp_path):
    files = [("data/A.txt", 1), ("data/a.txt", 2), ("data/b.txt", 3)]
    assert importchecks.case_clashes(files) == [("data/A.txt", "data/a.txt")]
    scan = projectimport._SScan(files=list(files))
    folder = tmp_path / "p"
    folder.mkdir()
    lines = projectimport._case_clashes(folder, scan)
    (folder / "probe").write_bytes(b"")
    insensitive = (folder / "PROBE").exists()
    assert len(lines) == 1 and ("is left out" in lines[0] or "both as written" in lines[0])
    assert len(scan.files) == (2 if insensitive else 3)
    assert not (folder / ".case-probe").exists()


def _stop_after(chunks: int):
    calls = {"n": 0}

    def say(text: str) -> None:
        if text == "":
            calls["n"] += 1
            if calls["n"] > chunks:
                raise KeyboardInterrupt

    return say


def test_a_stop_that_keeps_its_work_leaves_a_resumable_project_and_the_next_start_finds_it(tmp_path, monkeypatch):
    monkeypatch.setattr(projectimport, "CHUNK", 3)
    src, registry = make_mod(tmp_path / "src"), tmp_path / "cfg" / importresume.PENDING
    ctx = importchecks.CImportContext(m_registry=registry)
    with pytest.raises(KeyboardInterrupt):
        projectimport.import_mod(src, tmp_path / "proj", None, _stop_after(1), context=ctx, is_resumable=True)
    found = importresume.pending(registry)
    assert [p.folder for p in found] == [(tmp_path / "proj").resolve()] or [p.folder for p in found] == [
        Path(str(tmp_path / "proj").replace("\\", "/")).resolve()
    ]
    assert found[0].journal.state == importresume.STOPPED and found[0].journal.source == str(src)
    assert "mod-files" in {p.name for p in (tmp_path / "proj").iterdir()}
    report = importresume.resume(tmp_path / "proj", None, context=ctx)
    assert report.resumed == 6 and report.files == 7
    assert importresume.pending(registry) == [] and not importresume.journal_path(tmp_path / "proj").exists()
    proj = project.CProject.open(tmp_path / "proj")
    assert len(list(importmeta.read_manifest(proj.folder))) == 7
    diff = carriedpart.compare_trees(src, carriedpart.carried_root(proj), ignore=())
    assert not diff.different and set(diff.missing) <= {
        "modinfo.json",
        "data/graphics/x.cfg",
    }  # the config is the objects app's


def test_a_run_that_ended_in_the_middle_resumes_with_a_half_written_manifest_line(tmp_path, monkeypatch):
    monkeypatch.setattr(projectimport, "CHUNK", 3)
    src = make_mod(tmp_path / "src")
    with pytest.raises(KeyboardInterrupt):
        projectimport.import_mod(src, tmp_path / "proj", None, _stop_after(2), is_resumable=True)
    folder = tmp_path / "proj"
    manifest = folder / importmeta.FILES_FILE
    data = manifest.read_bytes()
    manifest.write_bytes(data[: len(data) - 7])  # the program died inside the last line
    first = json.loads(data.decode().splitlines()[-1])[0]
    (folder / "mod-files" / "anno117" / first).write_bytes(b"x")  # and inside the last file placed
    report = importresume.resume(folder)
    assert report.files == 7 and len(list(importmeta.read_manifest(folder))) == 7
    assert carriedpart.compare_trees(src, carriedpart.carried_root(project.CProject.open(folder))).different == []


def test_a_stop_without_keep_leaves_nothing_and_a_discard_removes_only_a_half_made_import(tmp_path, monkeypatch):
    monkeypatch.setattr(projectimport, "CHUNK", 3)
    src = make_mod(tmp_path / "src")
    with pytest.raises(KeyboardInterrupt):
        projectimport.import_mod(src, tmp_path / "gone", None, _stop_after(1))
    assert not (tmp_path / "gone").exists()
    registry = tmp_path / "pending.json"
    ctx = importchecks.CImportContext(m_registry=registry)
    with pytest.raises(KeyboardInterrupt):
        projectimport.import_mod(src, tmp_path / "kept", None, _stop_after(1), context=ctx, is_resumable=True)
    other = tmp_path / "other"
    other.mkdir()
    (other / "mine.txt").write_text("not ours")
    assert importresume.discard(other, registry) is False and (other / "mine.txt").exists()
    assert importresume.discard(tmp_path / "kept", registry) is True
    assert not (tmp_path / "kept").exists() and importresume.pending(registry) == []
    assert (src / "data/graphics/x.rdm").stat().st_size == 2048


def test_the_import_journal_keeps_its_stored_keys_and_reads_a_file_written_before_the_renames(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    journal = importresume.SJournal("src", use_links=False, is_hashed=False, folder_existed=True)
    importresume.write_journal(folder, journal)
    data = json.loads(importresume.journal_path(folder).read_text(encoding="utf-8"))
    assert (data["link"], data["hashed"], data["existed"]) == (False, False, True)
    assert not {"use_links", "is_hashed", "folder_existed"} & set(data)
    assert importresume.read_journal(folder) == journal


def test_resuming_with_the_source_gone_says_so_and_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(projectimport, "CHUNK", 3)
    src = make_mod(tmp_path / "src")
    with pytest.raises(KeyboardInterrupt):
        projectimport.import_mod(src, tmp_path / "proj", None, _stop_after(1), is_resumable=True)
    moved = src.rename(tmp_path / "moved")
    with pytest.raises(projectimport.CImportRefused, match="does not exist"):
        importresume.resume(tmp_path / "proj")
    assert (tmp_path / "proj").exists() and moved.exists()


def imported(tmp_path, link: bool = True):
    src = make_mod(tmp_path / "src")
    projectimport.import_mod(src, tmp_path / "proj", CSettings(), use_links=link)
    return src, tmp_path / "proj"


@pytest.mark.parametrize("link", [True, False])
def test_a_reimport_sorts_each_file_by_the_three_sides_and_never_overwrites_an_edit(tmp_path, link):
    src, folder = imported(tmp_path, link)
    mine = folder / "object-edits/anno117/data/graphics/x.cfg"  # the person edits this one, the mod does not
    replace(mine, b"my cfg")
    replace(folder / "mod-files/anno117/data/graphics/x.dds", b"my dds")  # both change this one
    replace(src / "data/graphics/x.dds", b"mod dds")
    replace(src / "data/graphics/x.rdm", b"new rdm")  # the mod only
    replace(src / "data/other/a.txt", b"a2")
    (src / "data/other/b.txt").unlink()  # the mod removes it
    (src / "data/other/new.txt").write_bytes(b"new")
    made = reimport.plan(folder, src)
    assert [c.rel for c in made.m_conflicts] == ["data/graphics/x.dds"]
    assert made.m_mine == ["data/graphics/x.cfg"]
    assert sorted(made.m_update) == ["data/graphics/x.rdm", "data/other/a.txt"]
    assert made.m_added == ["data/other/new.txt"] and made.m_removed == ["data/other/b.txt"]
    assert made.m_unchanged == 2  # the assets file and c.txt; x.cfg is the person's
    lines = reimport.apply(made)
    root = folder / "mod-files/anno117"
    assert "Taken from the mod: 3 files" in lines[0]
    assert (root / "data/graphics/x.rdm").read_bytes() == b"new rdm" and (root / "data/other/new.txt").exists()
    assert not (root / "data/other/b.txt").exists()
    assert mine.read_bytes() == b"my cfg" and (root / "data/graphics/x.dds").read_bytes() == b"my dds"
    assert (src / "data/graphics/x.dds").read_bytes() == b"mod dds"  # the mod was never changed
    again = reimport.plan(folder, src)
    assert again.m_update == [] and again.m_added == [] and again.m_removed == []
    assert sorted(again.m_mine) == ["data/graphics/x.cfg", "data/graphics/x.dds"] and again.m_conflicts == []
    again.dispose()


def test_a_reimport_takes_the_mods_file_for_a_conflict_the_person_chose_and_builds_to_the_mod(tmp_path):
    src, folder = imported(tmp_path)
    replace(folder / "mod-files/anno117/data/graphics/x.dds", b"my dds")
    replace(src / "data/graphics/x.dds", b"mod dds")
    made = reimport.plan(folder, src)
    reimport.apply(made, reimport.choose_all(made, reimport.TAKE))
    proj = project.CProject.open(folder)
    assert (folder / "mod-files/anno117/data/graphics/x.dds").read_bytes() == b"mod dds"
    from annotoolbox.objbuild import projectpart as objects

    project.build(proj, [carriedpart.PART, objects.PART], CSettings(), lambda _t: None)
    assert carriedpart.compare_trees(src, proj.build_dir).identical


def test_a_reimport_takes_a_changed_modinfo_only_where_the_person_did_not_edit_it(tmp_path):
    src, folder = imported(tmp_path)
    proj = project.CProject.open(folder)
    proj.name = "My own name"
    proj.save()
    (src / "modinfo.json").write_text(modinfo(extra={"Version": "1.1.0", "ModName": {"English": "Renamed"}}), "utf-8")
    made = reimport.plan(folder, src)
    assert made.m_identity == {"version": "1.1.0"}
    reimport.apply(made)
    now = project.CProject.open(folder)
    assert now.version == "1.1.0" and now.name == "My own name"
    kept = importmeta.kept_modinfo(now)
    assert kept is not None and b'"Version": "1.1.0"' in kept[1] and b"My own name" in kept[1]


def test_a_reimport_without_hashes_compares_sizes_and_says_so(tmp_path):
    src = make_mod(tmp_path / "src")
    projectimport.import_mod(src, tmp_path / "proj", None, is_hashed=False)
    replace(src / "data/other/a.txt", b"longer text")
    made = reimport.plan(tmp_path / "proj", src)
    assert made.m_update == ["data/other/a.txt"]
    assert any("no SHA-256" in line for line in made.lines())
    made.dispose()


def test_a_reimport_is_refused_for_a_handed_over_or_a_foreign_project(tmp_path):
    src, folder = imported(tmp_path)
    (folder / reimport.HANDOVER_RECORD).write_text("{}")
    with pytest.raises(projectimport.CImportRefused, match="undo the hand-over"):
        reimport.plan(folder, src)
    plain = tmp_path / "plain"
    project.CProject(plain, mod_id="plain-mod", name="Plain").save()
    with pytest.raises(projectimport.CImportRefused, match="not a project made by an import"):
        reimport.plan(plain, src)


def test_a_reimport_reads_a_zip_and_leaves_no_staging(tmp_path):
    import zipfile

    src, folder = imported(tmp_path)
    archive = tmp_path / "m.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in src.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(src).as_posix())
        zf.writestr("data/other/zipped.txt", b"z")
    made = reimport.plan(folder, archive)
    assert made.m_added == ["data/other/zipped.txt"] and made.m_unchanged == 7
    reimport.apply(made)
    assert not (folder / projectimport.STAGING).exists()
    assert (folder / "mod-files/anno117/data/other/zipped.txt").read_bytes() == b"z"


def test_a_refused_zip_in_a_reimport_leaves_no_staging(tmp_path):
    import zipfile

    _src, folder = imported(tmp_path)
    archive = tmp_path / "two.zip"
    with zipfile.ZipFile(archive, "w") as zf:  # two mods and no root one: refused after it was unpacked
        zf.writestr("a/modinfo.json", modinfo("mod-a"))
        zf.writestr("b/modinfo.json", modinfo("mod-b"))
    with pytest.raises(projectimport.CImportRefused):
        reimport.plan(folder, archive)
    assert not (folder / projectimport.STAGING).exists()


def test_a_manifest_line_that_is_not_a_list_is_cut_like_a_torn_one(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    manifest = folder / importmeta.FILES_FILE
    manifest.write_bytes(b'["a", 1, "x"]\n5\n["b", 2, "y"]\n')
    assert importresume.valid_manifest(folder) == {"a": (1, "x")}
    assert manifest.read_bytes() == b'["a", 1, "x"]\n'


def test_the_commands_resume_list_and_reimport(tmp_path, monkeypatch, capsys):
    import argparse

    from annotoolbox.suite import importcommands

    monkeypatch.setattr(projectimport, "CHUNK", 3)
    src = make_mod(tmp_path / "src")
    cfg = tmp_path / "cfg" / "settings.json"
    ctx = importchecks.CImportContext(m_registry=cfg.parent / importresume.PENDING)
    with pytest.raises(KeyboardInterrupt):
        projectimport.import_mod(src, tmp_path / "proj", None, _stop_after(1), context=ctx, is_resumable=True)
    parser = argparse.ArgumentParser()
    importcommands.register(parser.add_subparsers(dest="top"))
    args = parser.parse_args(["mod", "resume", str(tmp_path / "proj"), "--list", "--settings", str(cfg)])
    assert args.func(args) == 0 and "cut short" in capsys.readouterr().out
    args = parser.parse_args(["mod", "resume", "--list", "--settings", str(cfg)])  # no folder needed to list
    assert args.func(args) == 0 and "cut short" in capsys.readouterr().out
    args = parser.parse_args(["mod", "resume", "--settings", str(cfg)])
    assert args.func(args) == 2 and "folder" in capsys.readouterr().err  # resuming needs one
    args = parser.parse_args(["mod", "resume", str(tmp_path / "proj"), "--settings", str(cfg)])
    assert args.func(args) == 0 and not importresume.journal_path(tmp_path / "proj").exists()
    replace(src / "data/other/a.txt", b"a3")
    args = parser.parse_args(["mod", "reimport", str(tmp_path / "proj"), "--apply"])
    assert args.func(args) == 0
    assert (tmp_path / "proj/mod-files/anno117/data/other/a.txt").read_bytes() == b"a3"


def test_the_project_window_offers_a_half_made_import_and_updates_from_the_mod(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import apps, importdialog, shell
    from annotoolbox.suite.projectpanel import CProjectDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(projectimport, "CHUNK", 3)
    path = tmp_path / "cfg" / "settings.json"
    settings = CSettings(workspace=str(tmp_path / "ws"))
    dialog = CProjectDialog(shell.CSuiteWindow([], settings, path), apps.parts())
    src = make_mod(tmp_path / "src")
    ctx = importchecks.CImportContext(m_registry=importdialog.registry_of(path))
    with pytest.raises(KeyboardInterrupt):
        projectimport.import_mod(src, tmp_path / "proj", settings, _stop_after(1), context=ctx, is_resumable=True)
    monkeypatch.setattr(importdialog, "ask_pending", lambda _parent, found: ("resume", found[0]))
    dialog.offer_pending(is_waiting=True)
    app.processEvents()
    assert "files were placed already" in dialog.m_log.toPlainText()
    assert dialog.m_mod_id.text() == "edge-mod" and importresume.pending(importdialog.registry_of(path)) == []
    replace(src / "data/other/a.txt", b"a4")
    dialog.reimport(src, choices={}, is_waiting=True)
    app.processEvents()
    text = dialog.m_log.toPlainText()
    assert "1 changed in the mod only (taken)" in text and "Taken from the mod: 1 files" in text
