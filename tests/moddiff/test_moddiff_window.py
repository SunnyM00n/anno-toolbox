"""The Mod diff app's window and its jobs, offscreen, on a synthetic game and mods folder (the real ones are never read):
a question run through the manager, a profile's conflicts, the filter and the jump from a conflict to its target, an
archive question, the export, and a failed run said in the status line."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from tests.sources.sourcesfakes import asset_game, fake_mod

from annotoolbox.assetedit import assetgame
from annotoolbox.formats.rda import write_v22
from annotoolbox.moddiff import jobs
from annotoolbox.moddiff.window import CModDiffWindow
from annotoolbox.modmgr.manager import CModManager
from annotoolbox.suite.settings import CSettings

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
    return CSettings(anno117=str(game), mods=str(mods), workspace=str(tmp_path / "ws")), tmp_path


def text(model, index, column: int | None = None) -> str:
    return model.data(model.sibling(index.row(), index.column() if column is None else column, index))


@pytest.fixture
def win(qt, setup):
    window = CModDiffWindow(setup[0], is_synchronous=True)
    yield window
    window.close()


def test_the_window_lists_the_mods_and_answers_a_question(win):
    assert [win.m_mode_box.itemData(i) for i in range(win.m_mode_box.count())] == list(jobs.MODES)
    assert win.m_fields["a"][1].count() == 2 and win.m_profile_box.currentText() == "default"
    assert "anno117" in win.m_games  # the game of the last session is opened ahead of the first question
    win.m_fields["a"][1].setEditText("b-mod")
    win.run()
    assert win.m_report is not None and win.m_report.title == "b-mod against the game"
    model = win.m_tree.m_model
    assert model.rowCount() == 2  # the asset file and the carried files
    assert "1 target(s)" in win.m_summary.text() and win.m_export_action.isEnabled()
    top = model.index(0, 0)
    assert text(model, top) == ASSETS and model.rowCount(top) == 1
    target = model.index(0, 0, top)
    assert text(model, target) == "1001 Wood" and model.rowCount(target) == 1  # the values are made when asked for
    value = model.index(0, 0, target)
    assert text(model, value) == "Values/Product/Price" and text(model, value, 3) == "30"
    win.m_tree.setCurrentIndex(target)
    assert "Values/Product/Price" in win.m_detail.toPlainText()
    win.m_filter.setText("nothing like this")
    assert model.rowCount() == 0
    win.m_filter.setText("wood")
    assert model.rowCount() == 1


def test_a_zip_of_1800_mods_is_offered_beside_the_folder(win, tmp_path, monkeypatch):
    assert not win.m_zip_actions[0].isVisible()  # 117 reads no .zip
    win.m_game_box.setCurrentIndex(win.m_game_box.findData("anno1800"))
    assert win.m_zip_actions[0].isVisible() and win.m_zip_actions[1].isVisible()
    picked = tmp_path / "mods.zip"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(picked), ""))
    win.m_zip_actions[0].trigger()
    assert win.m_fields["a"][1].currentText() == str(picked) and win.mode() == jobs.MOD
    win.m_mode_box.setCurrentIndex(win.m_mode_box.findData(jobs.ARCHIVE))
    assert not win.m_zip_actions[0].isVisible()  # an archive question takes archives


def test_a_profile_shows_the_conflicts_and_a_row_opens_its_target(win):
    win.m_mode_box.setCurrentIndex(win.m_mode_box.findData(jobs.PROFILE))
    win.run()
    assert win.m_report is not None and len(win.m_report.conflicts) >= 2
    assert win.m_conflicts.m_model.rowCount() == len(win.m_report.conflicts)
    assert win.m_tabs.tabText(1).startswith("Conflicts (")
    kinds = [
        text(win.m_conflicts.m_model, win.m_conflicts.m_model.index(r, 1))
        for r in range(win.m_conflicts.m_model.rowCount())
    ]
    assert "same value" in kinds and "same file" in kinds
    win.m_filter.setText("zzz")
    win.m_conflicts.opened.emit(ASSETS, "1001")
    current = win.m_tree.currentIndex()
    assert current.isValid() and text(win.m_tree.m_model, current) == "1001 Wood"
    assert win.m_filter.text() == ""  # a filter that hides the target is cleared
    assert win.m_tabs.currentIndex() == 0


def test_an_archive_question_and_the_export(win, setup, monkeypatch):
    _settings, tmp_path = setup
    text = (asset_game(tmp_path / "other") / ASSETS).read_text(encoding="utf-8").replace("<Price>12", "<Price>77")
    archive = tmp_path / "mod.rda"
    archive.write_bytes(write_v22({ASSETS: text.encode()}))
    win.m_mode_box.setCurrentIndex(win.m_mode_box.findData(jobs.ARCHIVE))
    assert (
        win.m_fields["b"][1].isHidden()
        and [win.m_game_box.itemData(i) for i in range(win.m_game_box.count())][0] == "anno2070"
    )
    job = jobs.SJob(jobs.ARCHIVE, "anno117", str(archive))
    win.run(job)
    assert win.m_report is not None and [t.m_key for t in win.m_report.targets] == ["1001"]
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
    win.m_fields["a"][1].setEditText("no-such-mod")
    win.run()
    assert said and "no-such-mod" in said[0] and win.m_run_button.isEnabled()
    win.m_fields["a"][1].setEditText("")
    win.run()
    assert "first" in win.statusBar().currentMessage()


def test_the_jobs_find_mods_by_id_and_by_folder(setup):
    settings, tmp_path = setup
    manager = jobs.manager_for(settings, "anno117")
    assert isinstance(manager, CModManager) and jobs.manager_for(settings, "anno2070") is None
    assert jobs.find_mod("a-mod", manager, "anno117").m_mod_id == "a-mod"
    assert jobs.find_mod(str(tmp_path / "mods" / "b-mod"), None, "anno117").m_mod_id == "b-mod"
    with pytest.raises(ValueError):
        jobs.find_mod("nothing", manager, "anno117")
    with pytest.raises(FileNotFoundError):
        assetgame.open_installed(CSettings(workspace=str(tmp_path)), "anno117")


def test_a_question_from_another_app_is_shown_and_run(win):
    win.open_job(jobs.SJob(jobs.MOD, "anno117", "b-mod"))
    assert win.m_report is not None and win.m_report.title == "b-mod against the game"
    assert win.mode() == jobs.MOD and win.m_fields["a"][1].currentText() == "b-mod"
    win.open_job(jobs.SJob(jobs.PROFILE, "anno117", profile="default"))
    assert win.mode() == jobs.PROFILE and win.m_report.title == "profile default" and win.m_report.conflicts


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
    win.m_mode_box.setCurrentIndex(win.m_mode_box.findData(jobs.ARCHIVES))
    assert not win.m_fields["b"][1].isHidden()
    win.run(jobs.SJob(jobs.ARCHIVES, "anno117", *paths))
    assert win.m_report is not None and (win.m_report.left, win.m_report.right) == ("v0/mod.rda", "v1/mod.rda")
    wood = win.m_report.targets[0]
    assert (wood.m_key, wood.m_kind) == ("1001", "differs") and [(c.before, c.after) for c in wood.m_changes] == [
        ("50", "60")
    ]


def test_the_mods_app_asks_the_mod_diff(qt, tmp_path):
    from tests.modmgr.modmgrfakes import make_mods_folder

    from annotoolbox.modmgr import window as mw
    from annotoolbox.suite import links, livelink

    livelink.reset()
    mods = make_mods_folder(tmp_path)
    settings = CSettings(anno117="", mods=str(mods), workspace=str(tmp_path / "ws"), mods1800=str(tmp_path / "m1800"))
    asked = []
    handler = lambda *args: asked.append(args) or True  # noqa: E731
    links.withdraw(links.DIFF_MODS)
    window = mw.CModManagerWindow(settings, None, is_synchronous=True, lister=set)
    try:
        window._diff_actions()
        assert not window.m_diff_mod_action.isVisible() and not window.m_diff_profile_action.isVisible()  # no Mod diff
        links.provide(links.DIFF_MODS, handler)
        window.m_tree.setCurrentItem(window.m_tree.topLevelItem(0))
        window._diff_actions()
        assert window.m_diff_mod_action.isVisible() and window.m_diff_mod_action.isEnabled()
        window.diff_mod()
        window.diff_profile()
        assert asked[0][:3] == (jobs.MOD, "anno117", window.m_tree.current_card().mod_id)
        assert asked[1] == (jobs.PROFILE, "anno117", "", "", "default")
    finally:
        links.withdraw(links.DIFF_MODS, handler)
        window.close()


def test_no_row_is_left_out_of_a_large_report(qt):
    from annotoolbox.moddiff.difftree import CDiffTree
    from annotoolbox.moddiff.model import CHANGED, CONFLICT, VALUE, CTarget, SChange, SConflict, SReport
    from annotoolbox.moddiff.tables import CConflictTable

    targets = [CTarget("f.xml", str(i), CHANGED, f"n{i}", "T", [SChange("a/b", "1", "2")]) for i in range(30000)]
    tree = CDiffTree(is_synchronous=True)
    tree.set_report(SReport("t", "anno117", targets=targets))
    model = tree.m_model
    top = model.index(0, 0)
    assert model.rowCount() == 1 and model.rowCount(top) == 30000
    last = model.index(29999, 0, top)
    assert text(model, last) == "29999 n29999" and model.rowCount(last) == 1 and model.parent(last) == top
    tree.set_filter("n2999")  # n2999 and n29990 to n29999
    assert model.rowCount(model.index(0, 0)) == 11
    tree.set_filter("")
    assert tree.select_target("f.xml", "29999") and tree.describe(tree.currentIndex()).startswith("- **29999 n29999**")
    table = CConflictTable()
    table.set_conflicts(
        [
            SConflict(VALUE, CONFLICT, "f.xml", str(i), "", "p", ("a", "b"), ("1", "2"), "b", "name order")
            for i in range(12000)
        ]
    )
    assert table.m_model.rowCount() == 12000 and text(table.m_model, table.m_model.index(11999, 3)) == "p"


def test_a_jump_does_not_wait_for_the_filters_quarter_second(qt):
    from annotoolbox.moddiff.difftree import CDiffTree
    from annotoolbox.moddiff.model import CHANGED, CTarget, SChange, SReport

    tree = CDiffTree()  # the filter is worked out after a pause here, as in the window
    tree.set_report(
        SReport("t", "anno117", targets=[CTarget("f.xml", "7", CHANGED, "seven", "T", [SChange("a", "1", "2")])])
    )
    tree.set_filter("zzz")
    tree.set_filter("")
    assert tree.m_timer.isActive()  # waiting for the last key
    tree.settle()
    assert not tree.m_timer.isActive() and tree.select_target("f.xml", "7")


def test_mods_that_cannot_be_read_are_said_not_swallowed(qt, setup, monkeypatch):
    def fail(_settings, _key):
        raise OSError("the mods folder is not reachable")

    monkeypatch.setattr(jobs, "manager_for", fail)
    window = CModDiffWindow(setup[0], is_synchronous=True)
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
    noted = model.SConflict(model.GONE, model.CONFLICT, "f.xml", "5", "", "", who, notes, "f", model.LATE_PHASE)
    assert cells(noted)[4:] == [values, "f", decided]
    data = model.SConflict(model.VALUE, model.CONFLICT, "f.xml", "5", "", "p", who, notes, "f", model.LOAD_AFTER)
    assert cells(data)[4:] == [
        "a: added; b: removed; c: set a value; d: 3 value(s); e: 12 bytes; f: -",
        "f",
        "LoadAfter",
    ]


def test_a_mod_installed_after_the_window_opened_is_found_by_its_id(win, setup):
    fake_mod(setup[0].mods, "c-mod", {ASSETS: price(40)})
    win.m_fields["a"][1].setEditText("c-mod")
    win.run()
    assert win.m_report is not None and win.m_report.title == "c-mod against the game"
