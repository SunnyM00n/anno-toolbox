"""The mod manager's logic on a synthetic mods folder: the catalog, the load order and its problems, the loader's log,
install and remove, the running-game guard, the manager that joins them. Never the real mods folder."""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.modmgrfakes import ASSETS, ASSETS_PATH, make_mod, make_mods_folder, make_patch

from annotoolbox.modloader import loadlog
from annotoolbox.modloader.mods import Root, loader_roots
from annotoolbox.modloader.profilefile import ProfileText
from annotoolbox.modmgr import catalog, checks, gameguard, install, manager
from annotoolbox.suite import livelink


@pytest.fixture
def mods(tmp_path):
    return make_mods_folder(tmp_path)


def roots(mods: Path, game: Path | None = None, public: Path | None = None) -> list[Root]:
    return loader_roots("anno117", game, mods, public)


# the catalog


def test_the_roots_are_the_loaders_folders_in_its_order(tmp_path):
    found = loader_roots("anno117", tmp_path / "g", tmp_path / "docs", tmp_path / "pub")
    assert [(r.label, r.new_mods) for r in found] == [
        ("Ubisoft's examples", False),
        ("game", True),
        ("Documents", True),
        ("mod.io", True),
    ]
    assert found[0].path == tmp_path / "g" / "mods" / ".ubi"
    assert found[3].path == tmp_path / "pub" / "mod.io" / "11358" / "mods"
    assert [r.label for r in loader_roots("anno1800", None, tmp_path / "docs", tmp_path / "pub")] == ["Documents"]


def test_every_folder_is_a_card_with_its_modinfo_and_nested_mods(mods):
    make_mod(
        mods, "delta", Difficulty="cheat", GameSetup={"RequiresNewGame": True},
        Dependencies={"Require": ["alpha"], "Optional": ["x"], "Deprecate": ["old"]},
    )  # fmt: skip
    cat = catalog.scan(roots(mods), 8)
    names = [c.folder.name for c in cat.cards]
    assert names == ["alpha", "beta", "delta", "gamma"]  # profiles is no mod
    beta = cat.by_id("beta")
    assert [c.mod_id for c in beta.children] == ["beta-inner"] and beta.children[0].mod.parent is beta.mod
    delta = cat.by_id("delta")
    assert delta.difficulty == "cheat" and delta.setup == {
        "RequiresNewGame": True, "SafeToRemove": False, "Multiplayer": True, "Campaign": True,
    }  # fmt: skip
    assert delta.dependencies == {"Require": ["alpha"], "Optional": ["x"], "Deprecate": ["old"]}
    assert delta.description == "the delta mod" and delta.category == "Tools" and delta.name == "Delta"
    assert cat.card_of(beta.children[0].mod) is beta.children[0]


def test_a_folder_the_loader_would_drop_is_a_card_with_the_reason(mods):
    (mods / "broken").mkdir()
    (mods / "broken" / "modinfo.json").write_text("{not json", encoding="utf-8")
    make_mod(mods, "wrong-game", Anno=7)
    make_mod(mods, "-parked", "parked-mod")
    (mods / ".modmgr-staging").mkdir()  # a folder of ours: not shown
    make_mod(mods, "alpha-two", "alpha", version="2.0.0")
    cat = catalog.scan(roots(mods), 8)
    by_name = {c.folder.name: c for c in cat.cards}
    assert (
        "missing modinfo.json" in by_name["broken"].problem
        and "not the correct game version" in by_name["wrong-game"].problem
    )
    assert by_name["-parked"].parked and not by_name["-parked"].usable
    assert ".modmgr-staging" not in by_name
    assert by_name["alpha"].duplicate_of == str(mods / "alpha-two") and not by_name["alpha"].usable
    assert by_name["alpha-two"].usable and cat.by_id("alpha") is by_name["alpha-two"]
    assert "alpha" in cat.installed_ids() and "parked-mod" not in cat.installed_ids()


def test_state_follows_the_profile_and_enable_new_mods(mods, tmp_path):
    ubi = tmp_path / "game" / "mods" / ".ubi"
    make_mod(ubi, "example-ubi")
    cat = catalog.scan(roots(mods, tmp_path / "game"), 8)
    text = ProfileText("# EnableNewMods\nalpha\n# beta\n")
    st = {c.mod_id: catalog.state_of(c, text) for c in cat.cards}
    assert st["alpha"] == (catalog.ON, catalog.LISTED) and st["beta"] == (catalog.OFF, catalog.LISTED)
    assert st["gamma"] == (catalog.OFF, catalog.UNLISTED)
    on = ProfileText("EnableNewMods\nalpha\n")
    assert catalog.state_of(cat.by_id("gamma"), on) == (catalog.ON, catalog.NEW)
    assert catalog.state_of(cat.by_id("example-ubi"), on) == (catalog.OFF, catalog.UNLISTED)  # .ubi: only when listed
    assert catalog.state_of(cat.by_id("gamma"), None) == (catalog.OFF, catalog.UNLISTED)


# the order and the problems


def test_the_plan_orders_the_mods_and_explains_each_place(mods):
    cat = catalog.scan(roots(mods), 8)
    plan = checks.plan_profile(cat, ProfileText("alpha\nbeta\n"), "p")
    assert [r.mod.mod_id for r in plan.rows] == ["alpha", "beta", "beta-inner"]
    why = {r.mod.mod_id: r.why for r in plan.rows}
    assert why["beta"] == "listed; after alpha" and why["beta-inner"] == "nested in beta"
    assert plan.problems == []


def test_missing_incompatible_circle_and_replaced_mods_are_problems_before_the_start(mods):
    make_mod(mods, "loop-a", Dependencies={"LoadAfter": ["loop-b"]})
    make_mod(mods, "loop-b", Dependencies={"LoadAfter": ["loop-a"]})
    make_mod(mods, "oldie")
    make_mod(mods, "newer", Dependencies={"Deprecate": ["oldie"]})
    cat = catalog.scan(roots(mods), 8)
    plan = checks.plan_profile(cat, ProfileText("beta\ngamma\nalpha\nloop-a\nloop-b\nnewer\noldie\n"), "p")
    found = {i.code: i for i in plan.issues}
    assert "M03" in found and found["M03"].level == checks.PROBLEM  # gamma is incompatible with alpha
    assert "M02" in found and "loop-a" in found["M02"].text
    assert found["M06"].mod == "oldie" and found["M06"].level == checks.NOTE
    only_beta = checks.plan_profile(cat, ProfileText("beta\n"), "p")
    miss = [i for i in only_beta.issues if i.code == "M01"]
    assert miss and "installed but not on" in miss[0].text and miss[0].mod == "beta"
    gone = catalog.scan(roots(mods), 8)
    gone.cards = [c for c in gone.cards if c.folder.name != "alpha"]
    assert (
        "not found" in [i for i in checks.plan_profile(gone, ProfileText("beta\n")).issues if i.code == "M01"][0].text
    )


def test_the_plan_names_profile_entries_that_are_not_installed_and_broken_folders(mods):
    (mods / "broken").mkdir()
    cat = catalog.scan(roots(mods), 8)
    plan = checks.plan_profile(cat, ProfileText("alpha\nghost\n"), "p")
    codes = {(i.code, i.mod) for i in plan.issues}
    assert ("C03", "ghost") in codes and any(i.code == "C01" and "broken" in i.text for i in plan.issues)
    assert checks.plan_profile(cat, None).rows == []


def test_a_modinfo_the_loader_complains_about_is_a_problem(mods):
    make_mod(mods, "nodiff", Difficulty="bogus")
    cat = catalog.scan(roots(mods), 8)
    plan = checks.plan_profile(cat, ProfileText("nodiff\n"), "p")
    assert any(i.mod == "nodiff" and i.code == "I06" and i.level == checks.PROBLEM for i in plan.issues)


def test_enable_new_mods_puts_unlisted_mods_into_the_order(mods):
    cat = catalog.scan(roots(mods), 8)
    plan = checks.plan_profile(cat, ProfileText("EnableNewMods\n# gamma\n"), "p")
    assert [r.mod.mod_id for r in plan.rows if r.mod.parent is None] == ["alpha", "beta"]
    assert {r.mod.mod_id: r.why for r in plan.rows}["alpha"].startswith("EnableNewMods")


def test_the_deep_check_lists_every_operation_that_matches_nothing(mods):
    make_patch(mods / "alpha", '<ModOp Type="merge" GUID="999" Path="/Values"><Values/></ModOp>')
    make_patch(mods / "beta", '<ModOp Type="merge" GUID="1" Path="/Values"><Values/></ModOp>')
    cat = catalog.scan(roots(mods), 8)
    plan = checks.plan_profile(cat, ProfileText("alpha\nbeta\n"), "p")
    base = {ASSETS_PATH: ASSETS.encode()}
    found = checks.deep_check(plan, mods, base.get, lambda p: p in base, lint=False)
    assert [(i.mod, i.code) for i in found] == [("alpha", "op:nomatch")]
    assert "999" in found[0].text
    assert checks.deep_check(plan, mods, None, None, lint=False) == []
    linted = checks.deep_check(plan, mods, None, lambda p: False, lint=True)
    assert any(i.code == "P01" and i.mod == "alpha" for i in linted)  # a patch of a game file that is not there


# the log

LOG = """[2026-11-09 10:00:00.000] [info] Search mods...
[2026-11-09 10:00:00.001] [info] Path: "<game dir>/mods"
[2026-11-09 10:00:00.010] [info] Enabled mod: {base}/alpha  (alpha@1.0.0)
[2026-11-09 10:00:00.011] [info] Enabled mod: {base}/beta  (beta@1.0.0)
[2026-11-09 10:00:00.012] [info] Enabled mod: {base}/gamma  (gamma@1.0.0)
[2026-11-09 10:00:00.013] [info] Enabled mods: 3 (plus 1 nested)
[2026-11-09 10:00:00.100] [info] Load "[Tools] Alpha" alpha@1.0.0 (from "{base}/alpha")
[2026-11-09 10:00:02.600] [info] Load "[Tools] Beta" beta@1.0.0 (from "{base}/beta")
[2026-11-09 10:00:02.700] [info] beta: No matching node for Path "/Values/X" (assets.xml:12)
[2026-11-09 10:00:03.000] [warning] a line with no mod name at all
[2026-11-09 10:00:05.100] [info] Load "[Tools] Gamma" gamma@1.0.0 (from "{base}/gamma")
[2026-11-09 10:00:05.200] [info] alpha: AllowNoMatch Path /A (assets.xml:3)
[2026-11-09 10:00:06.000] [info] Patching completed in 6s
"""


def test_the_log_is_read_per_mod_with_times_and_problem_lines():
    log = loadlog.parse_log(LOG.format(base="C:/m").splitlines())
    assert log.order == ["alpha", "beta", "gamma"] and log.enabled == 3 and log.nested == 1 and log.seconds == 6
    assert [log.mods[m].seconds for m in log.order] == [2.5, 2.5, 0.9]
    assert all(log.mods[m].enabled and log.mods[m].loaded for m in log.order)
    assert [ln.split("] ")[-1][:6] for ln in log.mods["beta"].problems] == [
        "beta: ",
        "a line",
    ]  # by name, then by place
    assert log.mods["alpha"].problems and "AllowNoMatch" in log.mods["alpha"].problems[0]  # named, though gamma ran
    assert log.mods["gamma"].problems == [] and log.problem_count == 3 and log.searched == ["<game dir>/mods"]


def test_a_mod_enabled_but_not_loaded_and_a_missing_log(tmp_path):
    lines = [ln for ln in LOG.format(base="C:/m").splitlines() if "Load " not in ln or "Alpha" in ln]
    log = loadlog.parse_log(lines)
    assert log.mods["gamma"].enabled and not log.mods["gamma"].loaded and log.mods["gamma"].seconds is None
    assert loadlog.read_log(tmp_path) is None
    (tmp_path / "mod-loader.log").write_text(LOG.format(base="C:/m"), encoding="utf-8")
    assert loadlog.read_log(tmp_path).seconds == 6
    assert not loadlog.is_problem("[t] [info] Option: a: true") and loadlog.is_problem("[t] [error] x")


# install and remove


class Bin:
    """A recycle bin that only remembers what it was given (the real one is never touched)."""

    def __init__(self) -> None:
        self.got: list[Path] = []

    def __call__(self, path: Path) -> str:
        self.got.append(Path(path))
        shutil.rmtree(path)  # what the real bin does to the folder's place
        return "the test bin"


def test_a_mod_folder_is_copied_in_and_an_older_copy_goes_to_the_bin(tmp_path):
    src = make_mod(tmp_path / "src", "newmod", version="1.0.0")
    mods, bin_ = tmp_path / "mods", Bin()
    (src / "data").mkdir()
    (src / "data" / "f.txt").write_text("one", encoding="utf-8")
    [done] = install.install_path(src, mods, 8, bin_)
    assert done.mod_id == "newmod" and not done.replaced and (mods / "newmod" / "data" / "f.txt").read_text() == "one"
    assert src.is_dir() and bin_.got == []
    (src / "data" / "f.txt").write_text("two", encoding="utf-8")
    [again] = install.install_path(src, mods, 8, bin_)
    assert (
        again.replaced
        and [g.name for g in bin_.got] == ["old"]
        and (mods / "newmod" / "data" / "f.txt").read_text() == "two"
    )
    assert sorted(p.name for p in mods.iterdir()) == ["newmod"]  # no staging folder is left


def test_install_refuses_what_is_not_a_mod_or_would_overwrite_another_mod(tmp_path):
    mods = tmp_path / "mods"
    make_mod(mods, "taken", "someone-else")
    other = make_mod(tmp_path / "src", "taken", "mine")
    with pytest.raises(install.InstallError, match="another mod"):
        install.install_path(other, mods, 8)
    (tmp_path / "empty").mkdir()
    with pytest.raises(install.InstallError, match="not a mod"):
        install.install_path(tmp_path / "empty", mods, 8)
    wrong = make_mod(tmp_path / "src", "old-game", Anno=7)
    with pytest.raises(install.InstallError, match="not the correct game"):
        install.install_path(wrong, mods, 8)
    with pytest.raises(install.InstallError, match="neither a folder nor a .zip"):
        install.install_path(tmp_path / "nothing", mods, 8)
    with pytest.raises(install.InstallError, match="already"):
        install.install_path(mods / "taken", mods, 8)
    with pytest.raises(install.InstallError, match="inside that folder"):
        install.install_path(tmp_path, mods, 8)


def test_a_folder_of_mods_and_a_twin_modid_are_installed_with_a_note(tmp_path):
    pack = tmp_path / "pack"
    make_mod(pack, "one")
    make_mod(pack, "two")
    mods = tmp_path / "mods"
    make_mod(mods, "one-old", "one", version="0.1.0")
    done = install.install_path(pack, mods, 8, Bin())
    assert sorted(d.mod_id for d in done) == ["one", "two"]
    assert any("one-old" in n for d in done for n in d.notes)


def zipped(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return path


def test_a_zip_with_a_folder_around_the_mod_and_one_without(tmp_path):
    info = '{"Anno": 8, "ModID": "zipped", "Version": "1.0.0"}'
    mods = tmp_path / "mods"
    wrapped = zipped(tmp_path / "w.zip", {"Some Name/modinfo.json": info, "Some Name/data/x.txt": "x"})
    [a] = install.install_path(wrapped, mods, 8, Bin())
    assert a.folder == mods / "Some Name" and (a.folder / "data" / "x.txt").is_file()
    flat = zipped(tmp_path / "flat-1.2.3.zip", {"modinfo.json": info.replace("zipped", "flatmod"), "a.txt": "a"})
    [b] = install.install_path(flat, mods, 8, Bin())
    assert b.folder == mods / "flatmod"  # a zip's own name is no folder name: the ModID is
    assert sorted(p.name for p in mods.iterdir()) == ["Some Name", "flatmod"]


def test_a_zip_that_reaches_outside_or_is_no_zip_is_refused(tmp_path):
    mods = tmp_path / "mods"
    evil = zipped(tmp_path / "e.zip", {"../escape.txt": "x", "modinfo.json": "{}"})
    with pytest.raises(install.InstallError, match="outside its own folder"):
        install.install_path(evil, mods, 8)
    assert not (tmp_path / "escape.txt").exists()
    absolute = zipped(tmp_path / "a.zip", {"/abs.txt": "x"})
    with pytest.raises(install.InstallError, match="outside"):
        install.install_path(absolute, mods, 8)
    (tmp_path / "bad.zip").write_bytes(b"not a zip")
    with pytest.raises(install.InstallError, match="not a readable zip"):
        install.install_path(tmp_path / "bad.zip", mods, 8)
    assert not list(mods.glob(".modmgr-*"))


def test_remove_takes_a_mod_folder_of_a_mods_folder_to_the_bin_and_nothing_else(tmp_path):
    mods = tmp_path / "mods"
    folder = make_mod(mods, "gone")
    bin_ = Bin()
    assert install.remove_mod(folder, [mods], bin_) == "the test bin" and bin_.got == [folder]
    with pytest.raises(install.InstallError, match="not a mod folder"):
        install.remove_mod(mods, [mods], bin_)
    with pytest.raises(install.InstallError, match="not a mod folder"):
        install.remove_mod(folder / "sub", [mods], bin_)


@pytest.mark.skipif(os.name == "nt", reason="the fallback is for systems with no recycle bin")
def test_without_a_recycle_bin_the_mod_is_parked_beside_it(tmp_path):
    mods = tmp_path / "mods"
    folder = make_mod(mods, "gone")
    where = install.recycle(folder)
    assert not folder.exists() and Path(where).parent == mods / ".removed"
    assert catalog.scan([catalog.Root(mods, "x")], 8).cards[0:1] == []  # the loader skips it too


# the game and the manager


def test_the_guard_finds_the_game_by_its_process_and_says_what_a_change_means():
    livelink.reset()
    off = gameguard.state("anno117", lambda: {"explorer.exe"})
    on = gameguard.state("anno117", lambda: {"explorer.exe", "anno117.exe"})
    assert not off.busy and on.running and on.name == "Anno 117"
    assert gameguard.change_warning(off, "a", "a") == ""
    text = gameguard.change_warning(on, "other", "default")
    assert "next start" in text and "Shift+F3" in text and "default" in text and "not the active one" in text
    assert "not the active" not in gameguard.change_warning(on, "default", "default")
    assert not gameguard.state("anno1800", lambda: {"anno117.exe"}).running


def test_a_reload_without_a_link_says_what_to_do_by_hand_and_names_the_active_profile():
    livelink.reset()
    reply = gameguard.reload_in_game()
    assert reply.status == livelink.NOT_CONNECTED
    text = gameguard.reload_text(reply, "default")
    assert "Shift+F3" in text and "active profile (default)" in text


def settings_for(tmp_path, mods):
    return SimpleNamespace(
        anno117="", mods=str(mods), anno1800="", mods1800=str(tmp_path / "m1800"), workspace=str(tmp_path / "ws")
    )


def test_the_manager_reads_writes_and_plans_a_profile(tmp_path, mods):
    m = manager.for_settings(settings_for(tmp_path, mods), "anno117", lister=lambda: set(), trash=Bin())
    m.scan()
    assert m.active() == "default" and m.profile_names() == ["default"] and m.roots[0].path == mods
    assert [r.mod.mod_id for r in m.plan("default").rows] == ["alpha", "beta", "beta-inner"]
    m.set_mod("default", "gamma", True)
    m.set_mod("default", "alpha", False)
    text = (mods / "profiles" / "default.txt").read_text()
    assert text == "# EnableNewMods\nDisplayName default\n\n# alpha\nbeta\ngamma\n"
    m.set_enable_new("default", True)
    assert m.read("default").enable_new is True and m.read("") is None and m.read("nope") is None
    m.create_profile("copy", "default")
    assert m.read("copy").ids(True) == ["beta", "gamma"]
    assert m.warning("default") == ""


def test_the_manager_warns_while_the_game_runs_and_removes_a_mod(tmp_path, mods):
    bin_ = Bin()
    m = manager.for_settings(settings_for(tmp_path, mods), "anno117", lister=lambda: {"anno117.exe"}, trash=bin_)
    cat = m.scan()
    assert "is running" in m.warning("default")
    assert m.remove(cat.by_id("alpha")) == "the test bin" and bin_.got == [mods / "alpha"]
    with pytest.raises(install.InstallError, match="nested"):
        m.remove(cat.by_id("beta").children[0])
    [done] = m.install(make_mod(tmp_path / "src", "fresh"))
    assert done.folder == mods / "fresh" and "fresh" in m.scan().installed_ids()


def test_an_install_that_is_stopped_leaves_no_half_copied_mod(tmp_path, mods):
    class Stop(Exception):
        pass

    src = make_mod(tmp_path / "src", "big")
    (src / "data" / "deep").mkdir(parents=True)
    (src / "data" / "deep" / "x.xml").write_text("<x/>", encoding="utf-8")
    looks: list[str] = []

    def progress(text: str) -> None:
        looks.append(text)
        if len(looks) == 3:  # the mod's name, its folder, then a folder inside it: the copy is under way
            raise Stop

    before, placed, bin_ = sorted(p.name for p in mods.iterdir()), [], Bin()
    with pytest.raises(Stop):
        install.install_path(src, mods, 8, bin_, progress, placed)
    assert placed == [] and sorted(p.name for p in mods.iterdir()) == before and looks[1:] == ["", ""]
    del looks[:3]
    [done] = install.install_path(src, mods, 8, bin_, looks.append, placed)
    assert placed == [done] and (mods / "big" / "data" / "deep" / "x.xml").is_file() and "big" in looks[0]

    packed = tmp_path / "big.zip"
    shutil.make_archive(str(packed.with_suffix("")), "zip", tmp_path / "src")
    shutil.rmtree(mods / "big")
    looks.clear()
    with pytest.raises(Stop):  # stopped while it is unpacked
        install.install_path(packed, mods, 8, bin_, lambda t: (_ for _ in ()).throw(Stop) if looks else looks.append(t))
    assert sorted(p.name for p in mods.iterdir()) == before and "big.zip" in looks[0]


def test_the_deep_check_of_the_manager_runs_without_an_install(tmp_path, mods):
    make_patch(mods / "alpha", '<ModOp Type="merge" GUID="1" Path="/Values"><Values/></ModOp>')
    m = manager.for_settings(settings_for(tmp_path, mods), "anno117")
    m.scan()
    said = []
    found = m.deep_check("default", said.append)  # no game folder: the lint only, no operation is applied
    assert isinstance(found, list) and not [i for i in found if i.code.startswith("op:")]
    assert any(s.startswith("lint ") for s in said)


def test_1800_gets_its_own_mods_folder_and_no_modinfo_rules(tmp_path):
    old = tmp_path / "m1800"
    make_mod(old, "oldmod", Anno=0)
    m = manager.for_settings(settings_for(tmp_path, tmp_path / "mods"), "anno1800", lister=lambda: set())
    assert m.anno is None and m.mods_dir == old and [c.mod_id for c in m.scan().cards] == ["oldmod"]
    with pytest.raises(ValueError, match="no mod manager"):
        manager.ModManager("anno2070", tmp_path)


def test_a_change_is_announced_once_to_the_live_link(tmp_path, mods):
    from tests.fakes import FakeLink

    link = livelink.use(FakeLink())
    try:
        m = manager.for_settings(settings_for(tmp_path, mods), "anno117", lister=lambda: set(), trash=Bin())
        m.scan()
        said = m.set_mod("default", "gamma", True)
        assert "game reloaded" in said and link.names == ["reload.assets", "reload.texts"]
        link.sent.clear()
        assert m.set_mod("default", "gamma", True) == "" and not link.sent  # nothing changed: nothing sent
        fresh = make_mod(tmp_path / "src", "fresh")
        make_patch(fresh, "")
        [done] = m.install(fresh)
        assert "game reloaded" in done.notice and link.names[0] == "files.changed"
    finally:
        livelink.reset()
    assert "Shift+F3" in m.set_mod("default", "gamma", False)  # no game connected: the manual route


@pytest.mark.parametrize(
    ("language", "labels", "lint"),
    [
        ("en", ["Ubisoft's examples", "game", "Documents", "mod.io", "extra"], "lint alpha"),
        ("de", ["Beispiele von Ubisoft", "Spiel", "Dokumente", "mod.io", "zusätzlich"], "prüfe alpha"),
    ],
)
def test_the_roots_labels_and_the_progress_lines_follow_the_interface_language(tmp_path, mods, language, labels, lint):
    from annotoolbox.infra import i18n

    i18n.use(language)
    found = loader_roots("anno117", tmp_path / "g", tmp_path / "docs", tmp_path / "pub", [tmp_path / "more"])
    assert [catalog.root_label(r) for r in found] == labels
    make_patch(mods / "alpha", '<ModOp Type="merge" GUID="1" Path="/Values"><Values/></ModOp>')
    m = manager.for_settings(settings_for(tmp_path, mods), "anno117")
    m.scan()
    said = []
    m.deep_check("default", said.append)
    assert lint in said
