"""Dependencies and nested mods (``modloader.resolve``, ``mods`` nested discovery and order, the asset editor's import,
the project's build, ``mod lint``): synthetic mod sets and dependency graphs, with the probe's cases."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.sources.sourcesfakes import asset_game, fake_mod

from annotoolbox.assetedit import modimport
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.mod import modlint
from annotoolbox.modloader import mods
from annotoolbox.modloader.resolve import resolve, resolve_profile
from annotoolbox.suite import project, projectdeps
from annotoolbox.suite.settings import CSettings

ASSETS = "data/base/config/export/assets.xml"
OPS = '<ModOps><ModOp GUID="1001" Merge="Standard"><Name>{n}</Name></ModOp></ModOps>'


def ids(found) -> list[str]:
    return [m.m_mod_id for m in found]


def nested_set(root: Path):
    """``parent`` holding ``child`` (and its own ``grand``), a parked ``-off`` one and a plain folder ``assets``."""
    parent = fake_mod(root, "parent", {"data/p.txt": "p"}, ModName={"English": "Parent"})
    fake_mod(parent, "child", {"data/c.txt": "c"}, ModName={"English": "Child"})
    fake_mod(parent / "child", "grand", {}, ModName={"English": "Grand"})
    fake_mod(parent / "-off", "parked", {})
    fake_mod(parent / "plain" / "deeper", "deep", {})
    return parent


def test_nested_mods_are_found_below_the_parent_and_their_files_are_their_own(tmp_path):
    parent = nested_set(tmp_path)
    mod, _ = mods.read_mod(parent, 8)
    assert ids(mod.nested) == ["child", "deep"]  # a plain folder is walked through; a parked one holds none
    child = next(m for m in mod.nested if m.m_mod_id == "child")
    assert ids(child.nested) == ["grand"] and child.top is mod and child.nested[0].top is mod
    assert "child/data/c.txt" not in mod.files() and "data/p.txt" in mod.files()
    assert "-off/parked/modinfo.json" in mod.files()  # a parked folder is files of the parent
    assert "data/c.txt" in child.files()


def test_a_nested_folder_that_is_no_mod_is_noted_not_loaded(tmp_path):
    parent = fake_mod(tmp_path, "parent", {})
    fake_mod(parent, "wrong", {}, Anno=7)
    (parent / "bad").mkdir()
    (parent / "bad" / "modinfo.json").write_text("{nope")
    mod, _ = mods.read_mod(parent, 8)
    assert mod.nested == [] and len(mod.nested_notes) == 2


def test_the_parent_switches_its_nested_mods_on_and_a_profile_never_names_them(tmp_path):
    parent = nested_set(tmp_path)
    fake_mod(tmp_path, "other", {})
    found, _ = mods.find_mods([tmp_path], 8)
    assert ids(found) == ["other", "parent"]  # find_mods lists the mods of the folder; nested are inside them
    res = resolve_profile(found, mods.read_profile("parent\n"), ())
    assert set(ids(res.order)) == {"parent", "child", "grand", "deep"} and set(ids(res.nested)) == {
        "child",
        "grand",
        "deep",
    }
    only_child = resolve_profile(found, mods.read_profile("child\n"), ())
    assert only_child.order == []  # the nested id alone switches nothing on
    off = resolve_profile(found, mods.read_profile("# parent\nother\n"), ())
    assert ids(off.order) == ["other"]
    assert parent.is_dir()


def test_a_nested_mod_is_ordered_by_its_own_name_and_load_after_not_by_its_parent(tmp_path):
    parent = fake_mod(tmp_path, "parent", {}, ModName={"English": "M"}, Category={"English": "B"})
    fake_mod(parent, "zz-child", {}, ModName={"English": "A"}, Category={"English": "A"})
    fake_mod(parent, "late-child", {}, Category={"English": "A"}, ModName={"English": "A late"},
             Dependencies={"LoadAfter": ["*"]})  # fmt: skip
    fake_mod(tmp_path, "other", {}, ModName={"English": "O"}, Category={"English": "C"})
    found, _ = mods.find_mods([tmp_path], 8)
    res = resolve_profile(found, mods.read_profile("parent\nother\n"), ())
    assert ids(res.order) == ["zz-child", "parent", "other", "late-child"]


def test_the_same_mod_id_once_the_higher_version_the_later_when_equal_nested_ones_included(tmp_path):
    a = fake_mod(tmp_path, "a", {}, Version="1.0.0")
    fake_mod(a, "shared", {}, Version="1.2.0")
    b = fake_mod(tmp_path, "b", {})
    fake_mod(b, "shared", {}, Version="1.10.0")
    found, _ = mods.find_mods([tmp_path], 8)
    res = resolve_profile(found, mods.read_profile("a\nb\n"), ())
    shared = [m for m in res.order if m.m_mod_id == "shared"]
    assert [m.m_version for m in shared] == ["1.10.0"] and len(res.duplicates) == 1
    twin = fake_mod(tmp_path / "t2", "a", {}, Version="1.0.0")
    both, _ = mods.find_mods([tmp_path, tmp_path / "t2"], 8)
    assert next(m for m in both if m.m_mod_id == "a").m_folder == twin  # equal versions: the later folder


def test_required_dependencies_are_reported_missing_and_the_mod_loads_anyway(tmp_path):
    fake_mod(tmp_path, "needs", {}, Dependencies={"Require": ["there", "gone", "off"]})
    fake_mod(tmp_path, "there", {})
    fake_mod(tmp_path, "off", {})
    found, _ = mods.find_mods([tmp_path], 8)
    res = resolve_profile(found, mods.read_profile("needs\nthere\n# off\n"), ())
    assert "needs" in ids(res.order)
    assert {(m.dep, m.installed) for m in res.missing} == {("gone", False), ("off", True)}
    assert any("needs: is missing a dependency gone" in p for p in res.problems)


def test_optional_dependencies_neither_order_nor_gate_and_are_listed(tmp_path):
    fake_mod(tmp_path, "z-first", {}, ModName={"English": "A"}, Dependencies={"Optional": ["a-second", "nobody"]})
    fake_mod(tmp_path, "a-second", {}, ModName={"English": "B"})
    found, _ = mods.find_mods([tmp_path], 8)
    res = resolve_profile(found, mods.read_profile("z-first\na-second\n"), ())
    assert ids(res.order) == ["z-first", "a-second"] and res.missing == []  # the names decide, not Optional
    assert sorted((d, on) for _m, d, on in res.optional) == [("a-second", True), ("nobody", False)]


def test_load_after_orders_depth_first_and_ignores_what_is_not_there(tmp_path):
    for mod_id, name, after in (("a", "A", ["c", "ghost"]), ("b", "B", []), ("c", "C", [])):
        fake_mod(tmp_path, mod_id, {}, ModName={"English": name}, Dependencies={"LoadAfter": after})
    found, _ = mods.find_mods([tmp_path], 8)
    res = resolve_profile(found, mods.read_profile("a\nb\nc\n"), ())
    assert ids(res.order) == ["c", "a", "b"]  # a pulls c in front of itself, b stays in its place by name
    assert res.cycles == [] and res.missing == []


def test_a_load_after_circle_is_reported_and_cut(tmp_path):
    fake_mod(tmp_path, "x", {}, ModName={"English": "X"}, Dependencies={"LoadAfter": ["y"]})
    fake_mod(tmp_path, "y", {}, ModName={"English": "Y"}, Dependencies={"LoadAfter": ["z"]})
    fake_mod(tmp_path, "z", {}, ModName={"English": "Z"}, Dependencies={"LoadAfter": ["x"]})
    found, _ = mods.find_mods([tmp_path], 8)
    res = resolve_profile(found, mods.read_profile("x\ny\nz\n"), ())
    assert sorted(ids(res.order)) == ["x", "y", "z"] and len(res.cycles) == 1
    assert set(res.cycles[0]) == {"x", "y", "z"} and res.cycles[0][0] == res.cycles[0][-1]


def test_the_late_phase_follows_the_normal_one_and_a_normal_mod_cannot_follow_a_late_one(tmp_path):
    fake_mod(tmp_path, "late", {}, ModName={"English": "A"}, Dependencies={"LoadAfter": ["*"]})
    fake_mod(tmp_path, "norm", {}, ModName={"English": "Z"}, Dependencies={"LoadAfter": ["late"]})
    fake_mod(tmp_path, "later", {}, ModName={"English": "B"}, Dependencies={"LoadAfter": ["*", "norm"]})
    found, _ = mods.find_mods([tmp_path], 8)
    res = resolve_profile(found, mods.read_profile("late\nnorm\nlater\n"), ())
    assert ids(res.order) == ["norm", "late", "later"] and res.late_ignored == [("norm", "late")]


def test_incompatible_pairs_and_deprecation(tmp_path):
    fake_mod(tmp_path, "a", {}, Dependencies={"Incompatible": ["b", "c"]})
    fake_mod(tmp_path, "b", {}, Dependencies={"Incompatible": ["a"]})
    fake_mod(tmp_path, "c", {})
    fake_mod(tmp_path, "new", {}, Dependencies={"Deprecate": ["old"]})
    fake_mod(tmp_path, "old", {})
    fake_mod(tmp_path, "user", {}, Dependencies={"Require": ["old"], "LoadAfter": ["old"]})
    found, _ = mods.find_mods([tmp_path], 8)
    both = resolve_profile(found, mods.read_profile("a\nb\nnew\nold\nuser\n"), ())
    assert both.incompatible == [("a", "b")]  # once, though both name each other; c is not on
    assert "old" not in ids(both.order) and both.deprecated == [("new", "old")]
    assert both.missing == [] and ids(both.order).index("new") < ids(both.order).index("user")  # the id is redirected


def test_a_deprecated_parent_takes_its_nested_mods_with_it(tmp_path):
    old = fake_mod(tmp_path, "old", {})
    fake_mod(old, "kid", {})
    fake_mod(tmp_path, "new", {}, Dependencies={"Deprecate": ["old"]})
    found, _ = mods.find_mods([tmp_path], 8)
    res = resolve_profile(found, mods.read_profile("old\nnew\n"), ())
    assert ids(res.order) == ["new"]


def test_1800_names_of_the_dependency_lists_are_read(tmp_path):
    fake_mod(tmp_path, "m", {}, ModDependencies=["a"], OptionalDependencies=["b"], IncompatibleIds=["c"],
             DeprecateIds=["d"], LoadAfterIds=["e"])  # fmt: skip
    mod, _ = mods.read_mod(tmp_path / "m")
    assert (mod.m_require, mod.m_optional, mod.m_incompatible, mod.m_deprecate, mod.m_load_after) == (
        ["a"], ["b"], ["c"], ["d"], ["e"])  # fmt: skip


def test_the_probe_mods_resolve_as_the_probe_expects(tmp_path):
    """``scripts/modloader_probe.py``: a requires a mod that is not there and lists b as Optional; b is late."""
    fake_mod(tmp_path, "probe-modloader", {}, Category={"English": "Test"}, ModName={"English": "P"},
             Dependencies={"Require": ["probe-missing-require"], "Optional": ["probe-modloader-b", "probe-missing-optional"]})  # fmt: skip
    fake_mod(tmp_path, "probe-modloader-b", {}, Category={"English": "Test"}, ModName={"English": "B"},
             Dependencies={"LoadAfter": ["*"]})  # fmt: skip
    found, _ = mods.find_mods([tmp_path], 8)
    res = resolve_profile(found, mods.read_profile("probe-modloader\nprobe-modloader-b\n"), ())
    assert ids(res.order) == ["probe-modloader", "probe-modloader-b"]
    assert [m.dep for m in res.missing] == ["probe-missing-require"]


def test_a_single_mod_is_resolved_with_what_it_requires_from_its_neighbours(tmp_path):
    fake_mod(tmp_path, "lib", {}, Dependencies={"Require": ["lib-core"]})
    fake_mod(tmp_path, "lib-core", {})
    fake_mod(tmp_path, "unrelated", {})
    fake_mod(tmp_path, "main", {}, Dependencies={"Require": ["lib", "ghost"], "Optional": ["unrelated"]})
    found, _ = mods.find_mods([tmp_path], 8)
    main = next(m for m in found if m.m_mod_id == "main")
    res = resolve([main], [m for m in found if m is not main], allow_pull=True)
    assert (
        ids(res.order) == ["lib-core", "lib", "main"]
        or ids(res.order) == ["lib", "lib-core", "main"]
        or set(ids(res.order)) == {"main", "lib", "lib-core"}
    )
    assert res.pulled == {"lib": "main", "lib-core": "lib"}
    assert [m.dep for m in res.missing] == ["ghost"] and "unrelated" not in ids(res.order)
    assert any("lib (required by main)" in line for line in res.lines())


# the asset editor's import


@pytest.fixture
def game(tmp_path):
    return open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache")


def test_an_import_applies_nested_and_required_mods_in_order_and_says_how_they_came(tmp_path, game):
    root = tmp_path / "mods"
    main = fake_mod(root, "main", {ASSETS: OPS.format(n="main")}, Dependencies={"Require": ["lib", "ghost"]},
                    ModName={"English": "Main"})  # fmt: skip
    fake_mod(main, "inner", {ASSETS: OPS.format(n="inner")}, ModName={"English": "Aaa inner"})
    fake_mod(root, "lib", {ASSETS: OPS.format(n="lib")}, ModName={"English": "Lib"})
    fake_mod(root, "stranger", {ASSETS: OPS.format(n="stranger")})
    result = modimport.import_folder(game, main)
    assert ids(result.mods) == ["inner", "lib", "main"]  # by name: "[] Aaa inner", then Lib, then Main
    assert result.changes.m_mod_id == "main" and result.changes.m_assets[1001].set == {"Standard/Name": "main"}
    text = "\n".join(result.lines())
    assert "inner (nested in main)" in text and "lib (required by main)" in text
    assert "main: is missing a dependency ghost" in text and "stranger" not in text
    own = [m for m in result.mods if m.m_mod_id != "lib"]  # the required library is not carried: it stays a dependency
    assert sorted(c.folder for c in result.changes.m_carry) == sorted(str(m.m_folder) for m in own)
    assert result.changes.m_requires == ["lib"]
    assert not any(f.startswith("inner/") for f in result.carried)  # the nested mod's files are its own


def test_a_profile_import_lists_what_is_missing_and_incompatible(tmp_path, game):
    mods_dir = tmp_path / "mods"
    fake_mod(mods_dir, "a", {ASSETS: OPS.format(n="a")}, Dependencies={"Require": ["b"], "Incompatible": ["c"]})
    fake_mod(mods_dir, "b", {})
    fake_mod(mods_dir, "c", {})
    profile = mods_dir / "profiles" / "p.txt"
    profile.parent.mkdir()
    profile.write_text("a\nc\n", encoding="utf-8")
    result = modimport.import_profile(game, profile, [mods_dir])
    text = "\n".join(result.lines())
    assert "a: is missing a dependency b (installed, but not on)" in text and "a: is incompatible with c" in text


# the project's build


def _project(tmp_path, **kw) -> project.CProject:
    return project.CProject(tmp_path / "proj", mod_id="my-mod", name="My mod", game="anno117", **kw)


def test_a_project_declares_dependencies_that_reach_modinfo_and_are_checked(tmp_path):
    deps = {
        "Require": ["lib"],
        "LoadAfter": ["lib", "*"],
        "Incompatible": ["rival"],
        "Optional": [],
        "Deprecate": ["my-old"],
    }
    p = _project(tmp_path, dependencies=deps)
    p.save()
    again = project.CProject.open(p.folder)
    assert again.dependencies == deps
    for bad in ({"Require": ["My Mod"]}, {"Require": ["my-mod"]}, {"Friends": ["x"]}, {"Require": ["*"]}):
        with pytest.raises(ValueError):
            projectdeps.validate("my-mod", bad, [])
    part = project.SPart("a", "A", lambda _p: True, lambda _p, _s, _l: {"data/x.txt": b"x"})
    mods_dir = tmp_path / "mods"
    fake_mod(mods_dir, "lib", {})
    settings = CSettings()
    settings.mods = str(mods_dir)
    built = project.build(again, [part], settings, log=lambda _t: None)
    info = mods.read_jsonc(again.build_dir / "modinfo.json")
    assert info["Dependencies"] == {"Require": ["lib"], "Incompatible": ["rival"], "Deprecate": ["my-old"],
                                    "LoadAfter": ["lib", "*"]}  # fmt: skip
    assert not any("M01" in line for line in built.problems)
    (mods_dir / "lib" / "modinfo.json").unlink()
    (mods_dir / "lib").rmdir() if not any((mods_dir / "lib").iterdir()) else None
    again.dependencies = {"Require": ["gone"]}
    missing = project.build(again, [part], settings, log=lambda _t: None)
    assert any("M01" in line and "gone" in line for line in missing.problems)


def test_a_project_bundles_mods_as_nested_mods_in_the_loaders_layout(tmp_path):
    source = tmp_path / "elsewhere"
    lib = fake_mod(source, "my-lib", {"data/l.txt": "l"}, Version="2.0.0")
    fake_mod(lib, "inner", {})
    broken = source / "broken"
    broken.mkdir()
    p = _project(tmp_path, bundle=[str(lib), str(broken)], dependencies={"Require": ["my-lib"]})
    part = project.SPart("a", "A", lambda _p: True, lambda _p, _s, _l: {"data/x.txt": b"x"})
    mods_dir = tmp_path / "mods"
    fake_mod(mods_dir, "my-lib", {}, Version="1.0.0")
    settings = CSettings()
    settings.mods = str(mods_dir)
    built = project.build(p, [part], settings, log=lambda _t: None)
    assert (p.build_dir / "my-lib" / "modinfo.json").is_file() and (
        p.build_dir / "my-lib" / "inner" / "modinfo.json"
    ).is_file()
    mod, _ = mods.read_mod(p.build_dir, 8)
    assert ids(mod.nested) == ["my-lib"] and ids(mod.nested[0].nested) == ["inner"]
    text = "\n".join(built.problems)
    assert (
        "bundle broken" in text and "also installed as 1.0.0" in text and "M01" not in text
    )  # the bundle satisfies it
    install_to = tmp_path / "mods2"
    project.install(p, install_to, log=lambda _t: None)
    assert (install_to / "my-mod" / "my-lib" / "modinfo.json").is_file()


# mod lint


def test_mod_lint_reports_missing_cycles_incompatible_and_nested_mods_the_loader_skips(tmp_path):
    mods_dir = tmp_path / "mods"
    main = fake_mod(
        mods_dir,
        "main",
        {},
        Dependencies={"Require": ["gone", "there"], "LoadAfter": ["there"], "Incompatible": ["rival"]},
    )
    fake_mod(mods_dir, "there", {}, Dependencies={"LoadAfter": ["main"]})
    fake_mod(mods_dir, "rival", {})
    fake_mod(main / "-parked", "hidden", {})
    fake_mod(main, "wrong", {}, Anno=7)
    codes = {f.code: f for f in modlint.lint_mod(main, folders=[mods_dir]).findings}
    assert {"M01", "M02", "M04"} <= set(codes) and "M03" not in codes  # rival is installed, not on
    assert "gone" in codes["M01"].message and "there" not in codes["M01"].message
    profile = mods_dir / "profiles" / "p.txt"
    profile.parent.mkdir()
    profile.write_text("main\nrival\nthere\n", encoding="utf-8")
    on = modlint.lint_mod(main, folders=[mods_dir], profile=profile)
    assert "M03" in {f.code for f in on.findings}
    plain = modlint.lint_mod(main)
    assert not {"M01", "M02", "M03"} & {f.code for f in plain.findings}
    assert any("dependencies not checked" in n for n in plain.notes)


def test_mod_lint_reads_a_nested_mods_patches_against_its_own_root(tmp_path):
    parent = fake_mod(tmp_path, "parent", {})
    fake_mod(parent, "kid", {"data/x/new.xml": "<ModOps/>"})
    res = modlint.lint_mod(parent, has_base=lambda path: False)
    assert [f.where for f in res.findings if f.code == "P01"] == ["kid/data/x/new.xml"]
    assert res.files == 1
