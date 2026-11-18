"""The Mod diff app's window and its jobs, offscreen, on a synthetic game and mods folder (the real ones are never read):
a question run through the manager, a profile's conflicts, the filter and the jump from a conflict to its target, an
archive question, the export, and a failed run said in the status line."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from tests.fakes import asset_game, fake_mod

from annotoolbox.assetedit import assetgame
from annotoolbox.formats.rda import write_v22
from annotoolbox.moddiff import jobs
from annotoolbox.moddiff.window import ModDiffWindow
from annotoolbox.modmgr.manager import ModManager
from annotoolbox.suite.settings import Settings

ASSETS = "data/base/config/export/assets.xml"


def price(value: int) -> str:
    return f'<ModOps><ModOp Type="merge" GUID="1001" Path="/Values/Product"><Price>{value}</Price></ModOp></ModOps>'


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(tmp_path):
    game = asset_game(tmp_path / "117")
    mods = tmp_path / "mods"
    fake_mod(mods, "a-mod", {ASSETS: price(20), "data/x.dds": b"xx"})
    fake_mod(mods, "b-mod", {ASSETS: price(30), "data/x.dds": b"x"}, Dependencies={"LoadAfter": ["a-mod"]})
    (mods / "profiles").mkdir()
    (mods / "profiles" / "default.txt").write_text("DisplayName default\n\na-mod\nb-mod\n", encoding="utf-8")
    (mods / "profile.txt").write_text("ActiveProfile default", encoding="utf-8")
    return Settings(anno117=str(game), mods=str(mods), workspace=str(tmp_path / "ws")), tmp_path


def text(model, index, column: int | None = None) -> str:
    return model.data(model.sibling(index.row(), index.column() if column is None else column, index))


@pytest.fixture
def win(qt, setup):
    window = ModDiffWindow(setup[0], sync=True)
    yield window
    window.close()


def test_the_window_lists_the_mods_and_answers_a_question(win):
    assert [win.mode_box.itemData(i) for i in range(win.mode_box.count())] == list(jobs.MODES)
    assert win.fields["a"][1].count() == 2 and win.profile_box.currentText() == "default"
    assert "anno117" in win.games  # the game of the last session is opened ahead of the first question
    win.fields["a"][1].setEditText("b-mod")
    win.run()
    assert win.report is not None and win.report.title == "b-mod against the game"
    model = win.tree.model_
    assert model.rowCount() == 2  # the asset file and the carried files
    assert "1 target(s)" in win.summary.text() and win.export_action.isEnabled()
    top = model.index(0, 0)
    assert text(model, top) == ASSETS and model.rowCount(top) == 1
    target = model.index(0, 0, top)
    assert text(model, target) == "1001 Wood" and model.rowCount(target) == 1  # the values are made when asked for
    value = model.index(0, 0, target)
    assert text(model, value) == "Values/Product/Price" and text(model, value, 3) == "30"
    win.tree.setCurrentIndex(target)
    assert "Values/Product/Price" in win.detail.toPlainText()
    win.filter.setText("nothing like this")
    assert model.rowCount() == 0
    win.filter.setText("wood")
    assert model.rowCount() == 1


def test_a_zip_of_1800_mods_is_offered_beside_the_folder(win, tmp_path, monkeypatch):
    assert not win.zip_actions[0].isVisible()  # 117 reads no .zip
    win.game_box.setCurrentIndex(win.game_box.findData("anno1800"))
    assert win.zip_actions[0].isVisible() and win.zip_actions[1].isVisible()
    picked = tmp_path / "mods.zip"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(picked), ""))
    win.zip_actions[0].trigger()
    assert win.fields["a"][1].currentText() == str(picked) and win.mode() == jobs.MOD
    win.mode_box.setCurrentIndex(win.mode_box.findData(jobs.ARCHIVE))
    assert not win.zip_actions[0].isVisible()  # an archive question takes archives


def test_a_profile_shows_the_conflicts_and_a_row_opens_its_target(win):
    win.mode_box.setCurrentIndex(win.mode_box.findData(jobs.PROFILE))
    win.run()
    assert win.report is not None and len(win.report.conflicts) >= 2
    assert win.conflicts.model_.rowCount() == len(win.report.conflicts)
    assert win.tabs.tabText(1).startswith("Conflicts (")
    kinds = [
        text(win.conflicts.model_, win.conflicts.model_.index(r, 1)) for r in range(win.conflicts.model_.rowCount())
    ]
    assert "same value" in kinds and "same file" in kinds
    win.filter.setText("zzz")
    win.conflicts.opened.emit(ASSETS, "1001")
    current = win.tree.currentIndex()
    assert current.isValid() and text(win.tree.model_, current) == "1001 Wood"
    assert win.filter.text() == ""  # a filter that hides the target is cleared
    assert win.tabs.currentIndex() == 0


def test_an_archive_question_and_the_export(win, setup, monkeypatch):
    _settings, tmp_path = setup
    text = (asset_game(tmp_path / "other") / ASSETS).read_text(encoding="utf-8").replace("<Price>12", "<Price>77")
    archive = tmp_path / "mod.rda"
    archive.write_bytes(write_v22({ASSETS: text.encode()}))
    win.mode_box.setCurrentIndex(win.mode_box.findData(jobs.ARCHIVE))
    assert (
        win.fields["b"][1].isHidden()
        and [win.game_box.itemData(i) for i in range(win.game_box.count())][0] == "anno2070"
    )
    job = jobs.Job(jobs.ARCHIVE, "anno117", str(archive))
    win.run(job)
    assert win.report is not None and [t.key for t in win.report.targets] == ["1001"]
    out = tmp_path / "report.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "Markdown, summary (*.md)"))
    win.export_report()
    assert "Values/Product/Price" in out.read_text(encoding="utf-8") and "This is the summary" in out.read_text("utf-8")
    full = tmp_path / "full.md"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(full), "Markdown, every target and file (*.md)")
    )
    win.export_report()
    assert "This is the summary" not in full.read_text(encoding="utf-8")


def test_a_run_that_fails_says_why(win, monkeypatch):
    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _w, _t, text: said.append(text))
    win.fields["a"][1].setEditText("no-such-mod")
    win.run()
    assert said and "no-such-mod" in said[0] and win.run_button.isEnabled()
    win.fields["a"][1].setEditText("")
    win.run()
    assert "first" in win.statusBar().currentMessage()


def test_the_jobs_find_mods_by_id_and_by_folder(setup):
    settings, tmp_path = setup
    manager = jobs.manager_for(settings, "anno117")
    assert isinstance(manager, ModManager) and jobs.manager_for(settings, "anno2070") is None
    assert jobs.find_mod("a-mod", manager, "anno117").mod_id == "a-mod"
    assert jobs.find_mod(str(tmp_path / "mods" / "b-mod"), None, "anno117").mod_id == "b-mod"
    with pytest.raises(ValueError):
        jobs.find_mod("nothing", manager, "anno117")
    with pytest.raises(FileNotFoundError):
        assetgame.open_installed(Settings(workspace=str(tmp_path)), "anno117")


def test_a_question_from_another_app_is_shown_and_run(win):
    win.open_job(jobs.Job(jobs.MOD, "anno117", "b-mod"))
    assert win.report is not None and win.report.title == "b-mod against the game"
    assert win.mode() == jobs.MOD and win.fields["a"][1].currentText() == "b-mod"
    win.open_job(jobs.Job(jobs.PROFILE, "anno117", profile="default"))
    assert win.mode() == jobs.PROFILE and win.report.title == "profile default" and win.report.conflicts


def test_two_archives_are_a_question_of_their_own(win, setup):
    _settings, tmp_path = setup
    paths = []
    for n, price_text in enumerate(("50", "60")):
        text = (
            (asset_game(tmp_path / f"o{n}") / ASSETS)
            .read_text(encoding="utf-8")
            .replace("<Price>12", f"<Price>{price_text}")
        )
        folder = tmp_path / f"v{n}"
        folder.mkdir()
        (folder / "mod.rda").write_bytes(write_v22({ASSETS: text.encode()}))
        paths.append(str(folder / "mod.rda"))
    win.mode_box.setCurrentIndex(win.mode_box.findData(jobs.ARCHIVES))
    assert not win.fields["b"][1].isHidden()
    win.run(jobs.Job(jobs.ARCHIVES, "anno117", *paths))
    assert win.report is not None and (win.report.left, win.report.right) == ("v0/mod.rda", "v1/mod.rda")
    wood = win.report.targets[0]
    assert (wood.key, wood.kind) == ("1001", "differs") and [(c.before, c.after) for c in wood.changes] == [
        ("50", "60")
    ]


def test_the_mods_app_asks_the_mod_diff(qt, tmp_path):
    from tests.modmgrfakes import make_mods_folder

    from annotoolbox.modmgr import window as mw
    from annotoolbox.suite import links, livelink

    livelink.reset()
    mods = make_mods_folder(tmp_path)
    settings = Settings(anno117="", mods=str(mods), workspace=str(tmp_path / "ws"), mods1800=str(tmp_path / "m1800"))
    asked = []
    handler = lambda *args: asked.append(args) or True  # noqa: E731
    links.withdraw(links.DIFF_MODS)
    window = mw.ModManagerWindow(settings, None, sync=True, lister=set)
    try:
        window._diff_actions()
        assert not window.diff_mod_action.isVisible() and not window.diff_profile_action.isVisible()  # no Mod diff
        links.provide(links.DIFF_MODS, handler)
        window.tree.setCurrentItem(window.tree.topLevelItem(0))
        window._diff_actions()
        assert window.diff_mod_action.isVisible() and window.diff_mod_action.isEnabled()
        window.diff_mod()
        window.diff_profile()
        assert asked[0][:3] == (jobs.MOD, "anno117", window.tree.current_card().mod_id)
        assert asked[1] == (jobs.PROFILE, "anno117", "", "", "default")
    finally:
        links.withdraw(links.DIFF_MODS, handler)
        window.close()


def test_no_row_is_left_out_of_a_large_report(qt):
    from annotoolbox.moddiff.difftree import DiffTree
    from annotoolbox.moddiff.model import CHANGED, CONFLICT, VALUE, Change, Conflict, Report, Target
    from annotoolbox.moddiff.tables import ConflictTable

    targets = [Target("f.xml", str(i), CHANGED, f"n{i}", "T", [Change("a/b", "1", "2")]) for i in range(30000)]
    tree = DiffTree(sync=True)
    tree.set_report(Report("t", "anno117", targets=targets))
    model = tree.model_
    top = model.index(0, 0)
    assert model.rowCount() == 1 and model.rowCount(top) == 30000
    last = model.index(29999, 0, top)
    assert text(model, last) == "29999 n29999" and model.rowCount(last) == 1 and model.parent(last) == top
    tree.set_filter("n2999")  # n2999 and n29990 to n29999
    assert model.rowCount(model.index(0, 0)) == 11
    tree.set_filter("")
    assert tree.select_target("f.xml", "29999") and tree.describe(tree.currentIndex()).startswith("- **29999 n29999**")
    table = ConflictTable()
    table.set_conflicts(
        [
            Conflict(VALUE, CONFLICT, "f.xml", str(i), "", "p", ("a", "b"), ("1", "2"), "b", "name order")
            for i in range(12000)
        ]
    )
    assert table.model_.rowCount() == 12000 and text(table.model_, table.model_.index(11999, 3)) == "p"


def test_a_jump_does_not_wait_for_the_filters_quarter_second(qt):
    from annotoolbox.moddiff.difftree import DiffTree
    from annotoolbox.moddiff.model import CHANGED, Change, Report, Target

    tree = DiffTree()  # the filter is worked out after a pause here, as in the window
    tree.set_report(
        Report("t", "anno117", targets=[Target("f.xml", "7", CHANGED, "seven", "T", [Change("a", "1", "2")])])
    )
    tree.set_filter("zzz")
    tree.set_filter("")
    assert tree.timer.isActive()  # waiting for the last key
    tree.settle()
    assert not tree.timer.isActive() and tree.select_target("f.xml", "7")


def test_mods_that_cannot_be_read_are_said_not_swallowed(qt, setup, monkeypatch):
    def fail(_settings, _key):
        raise OSError("the mods folder is not reachable")

    monkeypatch.setattr(jobs, "manager_for", fail)
    window = ModDiffWindow(setup[0], sync=True)
    try:
        assert "not reachable" in window.statusBar().currentMessage()
    finally:
        window.close()


@pytest.mark.parametrize(
    ("language", "values", "decided"),
    [
        ("en", "a: added; b: removed; c: set a value; d: 3 value(s); e: 12 bytes; f: -", "late phase"),
        ("de", "a: hinzugefügt; b: entfernt; c: Wert gesetzt; d: 3 Wert(e); e: 12 Byte; f: -", "späte Phase"),
    ],
)
def test_a_conflicts_notes_are_shown_in_the_interface_language_and_the_games_data_as_it_is(language, values, decided):
    from annotoolbox.infra import i18n
    from annotoolbox.moddiff import model
    from annotoolbox.moddiff.tables import cells

    i18n.use(language)
    who = ("a", "b", "c", "d", "e", "f")
    notes = ("added", "removed", "set a value", "3 value(s)", "12 bytes", None)
    noted = model.Conflict(model.GONE, model.CONFLICT, "f.xml", "5", "", "", who, notes, "f", model.LATE_PHASE)
    assert cells(noted)[4:] == [values, "f", decided]
    data = model.Conflict(model.VALUE, model.CONFLICT, "f.xml", "5", "", "p", who, notes, "f", model.LOAD_AFTER)
    assert cells(data)[4:] == [
        "a: added; b: removed; c: set a value; d: 3 value(s); e: 12 bytes; f: -",
        "f",
        "LoadAfter",
    ]
