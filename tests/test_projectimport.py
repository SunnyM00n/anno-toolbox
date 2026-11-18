"""Importing a whole mod into a project and building it back (suite/projectimport.py, carriedpart.py, modinfokeep.py,
buildout.py): the sort, the identity, the GUID range, hard links and copies, the byte-identical round trip."""

from __future__ import annotations

import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from annotoolbox.suite import buildout, carriedpart, importmeta, modclassify, modinfokeep, project, projectimport
from annotoolbox.suite.settings import Settings

ASSETS = "data/base/config/export/assets.xml"
JSONC = """// a comment the author wrote
{
  "Anno": 8,
  "ModID": "big-mod",
  /* the name */
  "Version": "1.2.0",
  "Difficulty": "unchanged",
  "Category": {"English": "Gameplay"},
  "ModName": {"English": "Big mod"},
  "Description": {"English": "Many files."},
  "Scripts": {"Modules": ["ubi/big.lua"]},
  "Dependencies": {
    "Require": ["base-mod"],
    "LoadAfter": ["other-mod"]
  }
}
"""
MODOPS = b"""<?xml version="1.0" encoding="utf-8"?>
<ModOps>
  <!-- the author's comment -->
  <ModOp Type="add" Path="/Assets">
    <Asset><Values><Standard><GUID>2100000005</GUID><Name>one</Name></Standard></Values></Asset>
    <Asset><Values><Standard><GUID>2100000700</GUID><Name>two</Name></Standard></Values></Asset>
  </ModOp>
  <ModOp Type="merge" GUID="1000"><Asset><Values><Standard><GUID>1000</GUID></Standard></Values></Asset></ModOp>
</ModOps>
"""
OLD_1800 = """{
  "ModID": "Old_Mod",
  "Version": "1.0.0",
  "ModName": "Old mod",
  "Category": "Gameplay",
  "ModDependencies": ["base_mod"],
  "LoadAfterIds": ["other_mod"]
}
"""


def make_mod(root: Path, modinfo: str = JSONC, name: str = "modinfo.jsonc") -> Path:
    files: dict[str, bytes | str] = {
        name: modinfo,
        ASSETS: MODOPS,
        "data/base/config/gui/texts_english.xml": b"<ModOps><ModOp Type='add' Path='/Texts'/></ModOps>",
        "data/base/config/export/templates.xml": b"<ModOps/>",
        "data/graphics/x.rdm": bytes(range(256)) * 20,
        "data/graphics/x.dds": b"DDS " * 100,
        "data/graphics/x.cfg": b"cfg",
        "data/graphics/Y.CFG.XML": b"<Cfg/>",
        "data/islands/a.a7m": b"a7m",
        "data/islands/a.a7minfo": b"info",
        "data/ui/studio/generated/scene": b"phx",
        "data/sound/b.bnk": b"bnk",
        "data/extra/model.include.xml": b"<ModOps/>",
        "data/x.weird": b"?",
        "ubi/big.lua": b"print(1)",
        "data/big.json": b"{}",
        "data/empty.bin": b"",
        "nested/inner/modinfo.json": json.dumps({"Anno": 8, "ModID": "inner-mod", "Version": "0.5.0"}),
        "nested/inner/data/n.txt": b"nested file",
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
    return root


def do_import(tmp_path, mod: Path, link: bool = True, settings=None, say=lambda _t: None):
    folder = tmp_path / "proj"
    report = projectimport.import_mod(mod, folder, settings, say, link)
    return project.Project.open(folder), report


def build(proj: project.Project, parts=None) -> project.BuildResult:
    return project.build(proj, parts or [carriedpart.PART], Settings(), lambda _t: None)


def test_the_sort_names_each_files_kind_and_owner():
    cases = {
        ASSETS: ("patch", "assets"),
        "x/texts_german.xml": ("patch", "texts"),
        "x/properties.xml": ("patch", "carried"),
        "x/a.cfg.xml": ("config patch", "objects"),
        "x/a.include.xml": ("include", "carried"),
        "x/a.rdm": ("model", "carried"),
        "x/a.A7M": ("island", "islands"),
        "x/a.ctt": ("terrain", "carried"),
        "data/ui/studio/generated/scene": ("ui scene", "ui"),
        "x/a.bnk": ("sound", "sound"),
        "x/a.rdp": ("effect", "particles"),
        "modinfo.jsonc": ("modinfo", "carried"),
    }
    for rel, (kind, owner) in cases.items():
        got = modclassify.classify("anno117", rel)
        assert (got.kind, got.owner, got.understood) == (kind, owner, True), rel
    assert not modclassify.classify("anno117", "x/a.weird").understood
    assert modclassify.classify("anno1800", "data/ui/studio/generated/scene").owner == "carried"
    assert modclassify.extension("a/B.CFG.XML") == ".cfg.xml" and modclassify.extension("a/scene") == "(none)"


def test_an_import_makes_the_project_from_the_mods_identity_and_guids(tmp_path):
    mod = make_mod(tmp_path / "src")
    proj, report = do_import(tmp_path, mod)
    assert (proj.mod_id, proj.name, proj.version, proj.game, proj.category) == (
        "big-mod",
        "Big mod",
        "1.2.0",
        "anno117",
        "Gameplay",
    )
    assert proj.dependencies == {"Require": ["base-mod"], "LoadAfter": ["other-mod"]}
    assert proj.guid_range == (2_100_000_000, 2_100_009_999) and proj.guid_choice == "own"
    assert report.guid_added == 2 and report.guid_range == proj.guid_range
    assert [n[1:] for n in report.nested] == [("inner-mod", "0.5.0")]
    assert report.owners["assets"] == 1 and report.owners["islands"] == 2 and report.owners["ui"] == 1
    assert report.carried[".rdm"] == 1 and report.not_understood[".weird"] == 1
    assert report.files == 18 and report.linked + report.copied == 18
    meta = importmeta.read_meta(proj.folder)
    assert meta.modinfo == "modinfo.jsonc" and meta.identity["mod_id"] == "big-mod"
    manifest = {rel: (size, digest) for rel, size, digest in importmeta.read_manifest(proj.folder)}
    assert len(manifest) == 18 and manifest["data/empty.bin"][0] == 0 and len(manifest["data/big.json"][1]) == 64
    assert "modinfo.jsonc" not in manifest and any("Big mod" in line for line in report.lines())


def test_files_are_hard_linked_where_the_disk_allows_else_copied_and_the_source_never_changes(tmp_path):
    mod = make_mod(tmp_path / "src")
    proj, report = do_import(tmp_path, mod)
    placed = carriedpart.carried_root(proj) / "data/graphics/x.rdm"
    if report.linked:
        assert os.stat(placed).st_nlink >= 2 and report.copied == 0
    proj2, copied = do_import(tmp_path / "again", mod, link=False)
    assert copied.linked == 0 and copied.copied == 18
    assert os.stat(carriedpart.carried_root(proj2) / "data/graphics/x.rdm").st_nlink == 1
    before = (mod / "data/graphics/x.rdm").read_bytes()
    carriedpart.carried_root(proj).joinpath("data/graphics/x.rdm").unlink()
    (carriedpart.carried_root(proj) / "data/graphics/x.rdm").write_bytes(b"edited")
    assert (mod / "data/graphics/x.rdm").read_bytes() == before


def test_building_with_nothing_edited_gives_the_same_paths_and_bytes(tmp_path):
    mod = make_mod(tmp_path / "src")
    proj, _ = do_import(tmp_path, mod)
    result = build(proj)
    diff = carriedpart.compare_trees(mod, proj.build_dir)
    assert diff.identical, diff.lines()
    assert diff.same == 19 and (proj.build_dir / "modinfo.jsonc").read_bytes() == (mod / "modinfo.jsonc").read_bytes()
    assert (proj.build_dir / "nested/inner/data/n.txt").read_bytes() == b"nested file"
    assert not (proj.build_dir / "modinfo.json").exists() and result.files == 18
    again = build(proj)
    assert again.files == 18 and carriedpart.compare_trees(mod, proj.build_dir).identical


def test_a_copy_import_round_trips_too(tmp_path):
    mod = make_mod(tmp_path / "src", modinfo=json.dumps({"Anno": 8, "ModID": "plain"}), name="modinfo.json")
    proj, _ = do_import(tmp_path, mod, link=False)
    build(proj)
    diff = carriedpart.compare_trees(mod, proj.build_dir)
    assert diff.identical and diff.shared == 0 and diff.same == 19


def test_an_edited_identity_changes_only_those_fields_of_the_original_modinfo(tmp_path):
    mod = make_mod(tmp_path / "src")
    proj, _ = do_import(tmp_path, mod)
    proj.version, proj.dependencies = "2.0.0", {"Require": ["base-mod", "new-mod"], "LoadAfter": ["other-mod"]}
    proj.name = "Bigger mod"
    proj.save()
    build(proj)
    text = (proj.build_dir / "modinfo.jsonc").read_text(encoding="utf-8")
    expected = (
        JSONC.replace('"1.2.0"', '"2.0.0"')
        .replace('"Big mod"', '"Bigger mod"')
        .replace('"Require": ["base-mod"]', '"Require": ["base-mod", "new-mod"]')
    )
    assert text == expected and "// a comment the author wrote" in text and "/* the name */" in text
    again = project.Project.open(proj.folder)
    assert importmeta.changes_of(again, importmeta.read_meta(proj.folder)) == {
        "Version": "2.0.0", "ModName": "Bigger mod", "Require": ["base-mod", "new-mod"]}  # fmt: skip


def test_an_anno_1800_modinfo_is_read_and_patched_in_its_own_shape(tmp_path):
    mod = make_mod(tmp_path / "src", modinfo=OLD_1800, name="modinfo.json")
    proj, report = do_import(tmp_path, mod)
    assert proj.game == "anno1800" and proj.mod_id == "old-mod" and proj.name == "Old mod"
    assert any("Old_Mod" in note for note in report.notes)  # the ModID the project cannot hold
    assert proj.dependencies == {}  # underscores: ids the project cannot hold stay in the kept modinfo
    proj.dependencies = {"Require": ["fresh-mod"], "Optional": ["opt-mod"]}
    proj.save()
    build(proj)
    info = json.loads((proj.build_dir / "modinfo.json").read_text(encoding="utf-8"))
    assert info["ModID"] == "Old_Mod" and info["ModDependencies"] == ["fresh-mod"]
    assert info["OptionalDependencies"] == ["opt-mod"] and "Dependencies" not in info
    assert info["LoadAfterIds"] == ["other_mod"]


def test_the_modinfo_patch_writes_new_fields_in_place_whatever_the_layout():
    plain = b'{"Anno": 8, "ModID": "a"}'
    out = modinfokeep.patched(plain, {"Version": "2", "ModName": "N", "Require": ["x-mod"]}, "anno117")
    assert json.loads(out) == {"Anno": 8, "ModID": "a", "Version": "2", "ModName": {"English": "N"},
                               "Dependencies": {"Require": ["x-mod"]}}  # fmt: skip
    crlf = b'{\r\n  "ModID": "a",\r\n  "ModName": "Plain"\r\n}\r\n'
    out = modinfokeep.patched(crlf, {"ModName": "B", "Version": "1"}, "anno1800")
    assert out == b'{\r\n  "ModID": "a",\r\n  "ModName": "B",\r\n  "Version": "1"\r\n}\r\n'
    assert modinfokeep.patched(crlf, {}, "anno1800") is crlf
    with pytest.raises(modinfokeep.ModinfoError):
        modinfokeep.patched(b"[1]", {"Version": "1"}, "anno117")


def test_an_edit_next_to_the_carried_files_joins_modops_and_never_writes_into_the_source(tmp_path):
    mod = make_mod(tmp_path / "src")
    proj, _ = do_import(tmp_path, mod)
    edit = project.Part(
        "assets", "Assets", lambda _p: True, lambda *_a: {ASSETS: b"<ModOps><ModOp Type='remove' Path='/X'/></ModOps>"}
    )
    build(proj)  # the build's file is a link to the mod's now: the next build must replace it, not write into it
    build(proj, [carriedpart.PART, edit])
    built = (proj.build_dir / ASSETS).read_text(encoding="utf-8")
    assert built.index("2100000005") < built.index("<!-- Assets -->") < built.index("remove")
    assert (mod / ASSETS).read_bytes() == MODOPS
    assert (carriedpart.carried_root(proj) / ASSETS).read_bytes() == MODOPS
    clash = project.Part("other", "Other", lambda _p: True, lambda *_a: {"data/graphics/x.cfg": b"other bytes"})
    with pytest.raises(project.Clash):
        build(proj, [carriedpart.PART, clash])


def test_a_sync_keeps_what_is_current_and_removes_what_is_stale(tmp_path):
    src = tmp_path / "s.bin"
    src.write_bytes(b"x" * 10)
    out = tmp_path / "out"
    first = buildout.sync(out, {"a/s.bin": buildout.FileRef(str(src)), "b.txt": b"hi", "old.txt": b"o"})
    assert first["removed"] == 0 and sum(first[k] for k in ("linked", "copied")) == 1 and first["written"] == 2
    second = buildout.sync(out, {"a/s.bin": buildout.FileRef(str(src)), "b.txt": b"hi", "c/d.txt": b"new"})
    assert second["kept"] == 2 and second["removed"] == 1 and second["written"] == 1
    assert not (out / "old.txt").exists() and (out / "c/d.txt").read_bytes() == b"new"


def test_a_mod_that_adds_no_asset_takes_the_usual_new_range(tmp_path):
    mod = tmp_path / "src"
    mod.mkdir()
    (mod / "modinfo.json").write_text(json.dumps({"Anno": 8, "ModID": "small"}), encoding="utf-8")
    (mod / "data").mkdir()
    (mod / "data/a.txt").write_bytes(b"1")
    settings = Settings(own_reserve=[2_000_000_000, 2_000_999_999])
    proj, report = do_import(tmp_path, mod, settings=settings)
    assert report.guid_added == 0 and proj.guid_range == (2_000_000_000, 2_000_099_999)
    assert str(proj.folder.resolve()) in settings.known_projects
    assert proj.name == "src"


def test_a_zip_mod_is_unpacked_and_round_trips(tmp_path):
    mod = make_mod(tmp_path / "src")
    archive = tmp_path / "wrapped.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for path in sorted(p for p in mod.rglob("*") if p.is_file()):
            z.write(path, "Wrapper/" + path.relative_to(mod).as_posix())
    proj, report = do_import(tmp_path, archive)
    assert report.files == 18 and not (proj.folder / projectimport.STAGING).exists()
    build(proj)
    assert carriedpart.compare_trees(mod, proj.build_dir).identical


def test_an_import_is_refused_with_a_sentence_and_leaves_nothing(tmp_path):
    mod = make_mod(tmp_path / "src")
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("1")
    for source, folder, match in (
        (mod, full, "not empty"),
        (mod, mod / "inside", "inside"),
        (tmp_path / "missing", tmp_path / "p1", "missing|not found|exist"),
    ):
        with pytest.raises(projectimport.ImportRefused, match=match):
            projectimport.import_mod(source, folder)
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(projectimport.ImportRefused):
        projectimport.import_mod(bare, tmp_path / "p2")
    other = make_mod(tmp_path / "other", modinfo=json.dumps({"Anno": 5, "ModID": "x"}), name="modinfo.json")
    with pytest.raises(projectimport.ImportRefused, match="5"):
        projectimport.import_mod(other, tmp_path / "p3")
    assert not (tmp_path / "p2").exists() and not (tmp_path / "p3").exists() and not (mod / "inside").exists()


def test_stopping_an_import_removes_the_half_made_project_and_keeps_the_source(tmp_path):
    mod = make_mod(tmp_path / "src")
    calls = []
    before = len(list(mod.rglob("*")))

    def say(text: str) -> None:
        calls.append(text)
        if len(calls) > 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        projectimport.import_mod(mod, tmp_path / "proj", None, say)
    assert not (tmp_path / "proj").exists()
    assert len(list(mod.rglob("*"))) == before and (mod / "data/graphics/x.rdm").stat().st_size == 5120


def test_a_read_only_file_of_the_mod_goes_with_a_stopped_import_and_stays_read_only_in_the_mod(tmp_path):
    mod = make_mod(tmp_path / "src")
    locked = mod / "data/graphics/x.rdm"  # as the objects of a mod cloned with git are
    os.chmod(locked, stat.S_IREAD)
    calls = []

    def say(text: str) -> None:
        calls.append(text)
        if len(calls) > 2:
            raise KeyboardInterrupt

    try:
        with pytest.raises(KeyboardInterrupt):
            projectimport.import_mod(mod, tmp_path / "proj", None, say)
        assert not (tmp_path / "proj").exists()
        assert locked.stat().st_size == 5120 and not os.access(locked, os.W_OK)
    finally:
        os.chmod(locked, stat.S_IWRITE | stat.S_IREAD)


def test_a_build_drops_a_stale_read_only_link_without_touching_its_source(tmp_path):
    src = tmp_path / "s.bin"
    src.write_bytes(b"x" * 10)
    os.chmod(src, stat.S_IREAD)
    out = tmp_path / "out"
    try:
        buildout.sync(out, {"s.bin": buildout.FileRef(str(src)), "t.bin": buildout.FileRef(str(src))})
        again = buildout.sync(out, {"s.bin": b"other", "u.bin": b"u"})
        assert again["removed"] == 1 and again["written"] == 2 and (out / "s.bin").read_bytes() == b"other"
        assert src.read_bytes() == b"x" * 10 and not os.access(src, os.W_OK)
    finally:
        os.chmod(src, stat.S_IWRITE | stat.S_IREAD)


def test_a_modinfo_spelled_with_capitals_is_not_carried_as_a_second_one(tmp_path):
    mod = make_mod(tmp_path / "src", name="ModInfo.jsonc")
    if not (mod / "modinfo.jsonc").is_file():
        pytest.skip("this disk tells the two spellings apart, and so does the loader")
    proj, report = do_import(tmp_path, mod)
    assert report.files == 18 and not (carriedpart.carried_root(proj) / "ModInfo.jsonc").exists()
    build(proj)
    assert carriedpart.compare_trees(mod, proj.build_dir).identical


def test_the_added_guids_are_those_of_new_assets_only(tmp_path):
    path = tmp_path / "a.xml"
    path.write_bytes(
        b"<ModOps><Asset><Values><Standard><GUID>7</GUID></Standard></Values></Asset>"
        b"<ModOp Add='/Assets'><Asset><Values><Standard><GUID>8</GUID></Standard></Values></Asset></ModOp>"
        b"<ModOp Type='replace' GUID='9'><Asset><Values><Standard><GUID>9</GUID></Standard></Values></Asset></ModOp>"
        b"<ModOp Type='addNextSibling' GUID='3'><Asset><Values><Standard><GUID>10</GUID></Standard></Values></Asset>"
        b"</ModOp></ModOps>"
    )
    assert projectimport.added_guids(path) == [7, 8, 10]


def test_the_built_imported_mod_passes_the_modcheck_with_a_jsonc_modinfo(tmp_path):
    mod = make_mod(tmp_path / "src")
    proj, _ = do_import(tmp_path, mod)
    result = build(proj)
    assert not [p for p in result.problems if "I01" in p or "no modinfo" in p.lower()]


def test_the_project_window_imports_a_mod_with_its_report_and_opens_the_project(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import apps, shell
    from annotoolbox.suite.projectpanel import ProjectDialog

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "settings.json"
    settings = Settings(workspace=str(tmp_path / "ws"))
    dialog = ProjectDialog(shell.SuiteWindow([], settings, path), apps.parts())
    mod = make_mod(tmp_path / "src")
    dialog.import_mod(mod, tmp_path / "proj", wait=True)
    app.processEvents()
    log = dialog.log.toPlainText()
    assert "Big mod 1.2.0 imported for anno117" in log and "18 files" in log
    assert dialog.mod_id.text() == "big-mod" and settings.project == str(tmp_path / "proj")
    assert dialog.stop_button.isHidden() and dialog.buttons["Import a mod as a project..."].isEnabled()
    dialog.import_mod(mod, tmp_path / "proj", wait=True)  # not empty now: said, not raised
    assert "not empty" in dialog.log.toPlainText() and dialog.buttons["Save"].isEnabled()


def test_an_import_can_leave_the_hashes_out(tmp_path):
    mod = make_mod(tmp_path / "src")
    folder = tmp_path / "proj"
    projectimport.import_mod(mod, folder, None, hash_files=False)
    sizes = {rel: (size, digest) for rel, size, digest in importmeta.read_manifest(folder)}
    assert sizes["data/graphics/x.rdm"] == (5120, "") and len(sizes) == 18
