import json
from pathlib import Path

import pytest

from annotoolbox.editor import editorapp, islandeditor
from annotoolbox.othergames import installs
from annotoolbox.suite.settings import Settings, is_2070


def test_2070_is_told_from_2205(tmp_path):
    for name, exe in (("Anno 2070", "Anno5.exe"), ("Anno 2205", "Anno2205.exe")):
        (tmp_path / name / "maindata").mkdir(parents=True)
        (tmp_path / name / "maindata" / "data0.rda").write_bytes(b"")
        (tmp_path / name / exe).write_bytes(b"")
    assert is_2070(tmp_path / "Anno 2070") and not is_2070(tmp_path / "Anno 2205")


def test_settings_round_trip_and_problems(tmp_path):
    s = Settings(anno117=str(tmp_path / "nope"), anno2070=str(tmp_path), mods=str(tmp_path / "mods"))
    path = tmp_path / "settings.json"
    s.save(path)
    assert bytes([13]) not in path.read_bytes()  # LF on every platform
    loaded = Settings.load(path)
    assert loaded.anno117 == s.anno117 and loaded.workspace == s.workspace
    problems = loaded.problems()
    assert any("Anno 117" in p for p in problems) and any("Anno 2070" in p for p in problems)


def test_a_stopped_job_ends_at_its_next_log_line_and_a_step_that_must_run_whole_is_not_cut():
    import threading

    from annotoolbox.infra.i18n import tr

    held, release = threading.Event(), threading.Event()

    def long(job):
        job.log("first")
        held.set()
        assert release.wait(30)
        job.note("the step that runs whole")  # a stop does not cut it short
        job.log("second")  # it ends here
        return "never"

    stopped = editorapp.Job("long", long)
    assert held.wait(30)
    stopped.stop()
    release.set()
    assert stopped.wait(30) and stopped.state == "stopped" and stopped.result is None
    assert stopped.lines == ["first", "the step that runs whole", tr("islands.job_stopped", name="long")]
    ok = editorapp.Job("ok", lambda job: job.log("step") or 7)
    assert ok.wait(30)
    ok.stop()  # a job that has ended stays as it ended
    assert ok.state == "done" and ok.result == 7


def test_a_job_keeps_its_log_and_its_failure():
    ok = editorapp.Job("ok", lambda job: job.log("step") or 7)
    bad = editorapp.Job("bad", lambda job: 1 / 0)
    for job in (ok, bad):
        job._thread.join(5)
    assert ok.state == "done" and ok.result == 7 and ok.status()["lines"] == ["step"]
    assert bad.state == "failed" and bad.lines[-1] == "failed: division by zero"
    assert any("ZeroDivisionError" in line for line in bad.lines)  # the traceback too
    assert bad.status(since=len(bad.lines))["lines"] == []


def test_the_app_keeps_its_settings_and_opens_nothing_without_games(tmp_path):
    settings = Settings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods"))
    path = tmp_path / "settings.json"
    settings.save(path)
    app = editorapp.App(path)
    assert app.island is None and app.settings.problems()  # no games in tmp_path
    app.set_settings({"mods": "m"})
    assert app.settings.mods == "m" and json.loads(path.read_text())["mods"] == "m"
    with pytest.raises(ValueError, match="cannot open"):
        app.open("anno1234", "x")


class _Opened:
    """What ``App.open_path`` hands to ``IslandEditor``."""

    calls: list = []

    def __init__(self, files, a7m_path, cache, mod_assets="", edits=None, tree2070=None):
        self.edit_meta = {}
        _Opened.calls.append((a7m_path, files.mod_root, mod_assets, edits, tree2070))


def test_a_job_whose_library_exits_ends_failed_not_running():
    """A SystemExit in the worker thread used to end it silently: the state stayed "running" and every
    later job was refused as "still running"."""

    def work(job):
        raise SystemExit("no SimpleCfg template object in the donor")

    job = editorapp.Job("exit", work)
    job._thread.join(5)
    assert job.state == "failed" and "no SimpleCfg template" in job.lines[-1]


def test_settings_need_absolute_folders_for_what_is_cleared(tmp_path):
    ok = Settings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods"))
    assert not any("mods folder must" in p or "workspace must" in p for p in ok.problems())
    for field in ("mods", "workspace"):
        for value in ("", "  ", "relative/folder"):
            bad = Settings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods"))
            setattr(bad, field, value)
            assert any(f"{'mods folder' if field == 'mods' else field} must" in p for p in bad.problems()), (
                field,
                value,
            )


def test_installing_never_replaces_a_relative_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "built"
    source.mkdir()
    (source / "modinfo.json").write_text("{}", encoding="utf-8")
    (tmp_path / "anno2070-islands").mkdir()  # what an empty mods folder would have meant: the working directory's
    (tmp_path / "anno2070-islands" / "keep.txt").write_text("mine", encoding="utf-8")
    app = editorapp.App(tmp_path / "settings.json")
    app.settings.mods = ""
    job = editorapp.Job("x", lambda j: None)
    job._thread.join(5)
    try:
        app._copy_mod(job, source, editorapp.MOD_ID)
        raise AssertionError("copied over a relative target")
    except RuntimeError as exc:
        assert "absolute" in str(exc)
    assert (tmp_path / "anno2070-islands" / "keep.txt").read_text(encoding="utf-8") == "mine"
    app.settings.mods = str(tmp_path / "mods")
    app._copy_mod(job, source, editorapp.MOD_ID)
    assert (tmp_path / "mods" / editorapp.MOD_ID / "modinfo.json").is_file()


def test_a_failed_install_copy_leaves_the_installed_mod_and_another_mods_folder_is_never_replaced(
    tmp_path, monkeypatch
):
    """The install deleted the installed mod and then copied: a copy that failed half way left half a mod."""
    from annotoolbox.modloader import modswap

    source = tmp_path / "built"
    (source / "data").mkdir(parents=True)
    (source / "modinfo.json").write_text(json.dumps({"ModID": editorapp.MOD_ID}), encoding="utf-8")
    (source / "data" / "island.a7m").write_bytes(b"v1")
    app = editorapp.App(tmp_path / "settings.json")
    app.settings.mods = str(tmp_path / "mods")
    binned: list = []
    app.trash = binned.append
    job = editorapp.Job("x", lambda j: None)
    job._thread.join(5)
    app._copy_mod(job, source, editorapp.MOD_ID)
    installed = tmp_path / "mods" / editorapp.MOD_ID
    (source / "data" / "island.a7m").write_bytes(b"v2")

    def fail(src, dst, **_kw):
        Path(dst).mkdir(parents=True)
        (Path(dst) / "modinfo.json").write_text("{}", encoding="utf-8")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(modswap.shutil, "copytree", fail)
    with pytest.raises(modswap.InstallError, match="could not be copied"):
        app._copy_mod(job, source, editorapp.MOD_ID)
    monkeypatch.undo()
    assert (installed / "data" / "island.a7m").read_bytes() == b"v1" and not binned
    app._copy_mod(job, source, editorapp.MOD_ID)
    assert (installed / "data" / "island.a7m").read_bytes() == b"v2" and len(binned) == 1  # the old copy: the bin
    other = tmp_path / "mods" / editorapp.MOD_ID_117
    other.mkdir()
    (other / "modinfo.json").write_text(json.dumps({"ModID": "someone-elses"}), encoding="utf-8")
    with pytest.raises(modswap.InstallError, match="someone-elses"):
        app._copy_mod(job, source, editorapp.MOD_ID_117)
    assert json.loads((other / "modinfo.json").read_text(encoding="utf-8"))["ModID"] == "someone-elses"
    with pytest.raises(modswap.InstallError, match="not a folder name"):
        app._copy_mod(job, source, "..")


def test_a_missing_game_shows_on_its_tab_and_blocks_only_what_needs_it(tmp_path):
    """The editor needed 117 and 2070 (and 117's Documents folder) before any tab showed, even for 1800 alone."""
    exe = tmp_path / "1800" / "Bin" / "Win64" / "Anno1800.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    missing_docs = tmp_path / "Documents" / "Anno 117 - Pax Romana" / "mods"  # the game never started yet
    settings = Settings(anno1800=str(tmp_path / "1800"), mods=str(missing_docs), workspace=str(tmp_path / "ws"))
    settings.save(tmp_path / "settings.json")
    app = editorapp.App(tmp_path / "settings.json")
    assert app.problems() == []  # every tab shows
    cat = app.catalogue()
    assert "Anno 117 not found" in cat["anno117"]["note"] and "Anno 2070 not found" in cat["anno2070"]["note"]
    assert cat["anno117"]["native"] == [] and "not found" not in cat["anno1800"]["note"]
    with pytest.raises(RuntimeError, match="Anno 117 not found"):
        app.build117()
    with pytest.raises(RuntimeError, match="Anno 2070 not found"):
        app.build("n_l03")
    with pytest.raises(RuntimeError, match="parent does not exist"):
        app.install()
    assert not missing_docs.parent.exists()  # nothing made a folder there
    only117 = editorapp.App(tmp_path / "settings.json", games=("anno117", "anno1800"))
    assert list(only117.catalogue()) == ["anno117", "anno1800"]


def test_the_window_takes_any_games_for_its_editor():
    from annotoolbox.suite import toolbox

    args = toolbox.window_parser().parse_args(["--games", "anno117, anno2205,anno117"])
    assert args.games == ("anno117", "anno2205")
    with pytest.raises(SystemExit):
        toolbox.window_parser().parse_args(["--games", "anno9"])


def test_every_game_has_native_edited_and_converted_lists(tmp_path):
    settings = Settings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods"))
    settings.workspace = str(tmp_path / "ws")
    path = tmp_path / "settings.json"
    settings.save(path)
    app = editorapp.App(path)
    edits = app.edits_dir
    edits.mkdir(parents=True)
    (edits / "1800_moderate_01.json").write_text('{"source": "data/x/moderate_01.a7m", "game": "anno1800"}')
    cat = app.catalogue()
    assert list(cat) == ["anno117", "anno1800", "anno2205", "anno2070", "anno1404", "anno1701"]  # newest first
    assert all({"native", "edited", "converted", "buildable"} <= set(e) for e in cat.values())
    assert cat["anno1800"]["edited"] == [{"source": "data/x/moderate_01.a7m", "name": "moderate_01"}]
    assert cat["anno1800"]["buildable"] and cat["anno117"]["buildable"] and cat["anno2205"]["buildable"]
    with pytest.raises(ValueError, match="no island build"):
        app.convert("anno1800", "x", "anno117")
    assert installs.GAMES["anno1404"].edit_prefix == "1404_"


def test_an_app_restricted_to_1800_deals_with_that_game_only(tmp_path):
    exe = tmp_path / "Bin" / "Win64" / "Anno1800.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    settings = Settings(anno1800=str(tmp_path), workspace=str(tmp_path / "ws"))
    settings.save(tmp_path / "settings.json")
    app = editorapp.App(tmp_path / "settings.json", games=("anno1800",))
    assert app.problems() == []  # no 117, no 2070 and no 117 mods folder asked for
    assert Settings(workspace=str(tmp_path)).problems(("anno1800",))[0].startswith("Anno 1800 not found")
    assert list(app.catalogue()) == ["anno1800"] and app.routes() == []
    assert app.islands()["games"].keys() == {"anno1800"} and app.catalogue()["anno1800"]["buildable"]
    with pytest.raises(ValueError, match="only opens anno1800"):
        app.open("anno117", "data/x.a7m")
    with pytest.raises(ValueError, match="unknown games"):
        editorapp.App(tmp_path / "settings.json", games=("anno9",))


def test_building_1800_islands_needs_the_install_and_an_absolute_mods_folder_to_install(tmp_path):
    settings = Settings(anno1800=str(tmp_path), workspace=str(tmp_path / "ws"))
    settings.save(tmp_path / "settings.json")
    app = editorapp.App(tmp_path / "settings.json", games=("anno1800",))
    with pytest.raises(RuntimeError, match="mods folder"):
        app.build1800(install=True)
    with pytest.raises(RuntimeError, match="Anno 1800 not found"):
        app.build1800()


def _wait(job):
    job._thread.join(20)
    assert job.state == "done", job.lines
    return job


def test_every_archive_game_builds_installs_and_restores_through_one_record(tmp_path):
    from test_oldenginebuild import ISLE, edits, install_1404

    game = install_1404(tmp_path / "game")
    ws = tmp_path / "ws"
    settings = Settings(anno1404=str(game), workspace=str(ws), project="")
    settings.save(tmp_path / "settings.json")
    app = editorapp.App(tmp_path / "settings.json")
    app.edits_dir.mkdir(parents=True)
    edits(app.edits_dir, "1404_", ISLE, objects={"2070:12": {"deleted": True}})
    own = (game / "maindata/data2.rda").read_bytes()
    assert not app.installed("anno1404") and not app.installed("anno2070") and not app.installed("anno9")
    job = _wait(app.build_archives("anno1404"))  # built into the workspace, the game untouched
    assert job.result == [ISLE] and (ws / "anno1404-archives/maindata/data2.rda").is_file()
    assert (game / "maindata/data2.rda").read_bytes() == own and not app.installed("anno1404")
    _wait(app.build_archives("anno1404", install=True))
    assert app.installed("anno1404") and (game / "maindata/data2.rda").read_bytes() != own
    _wait(app.restore("anno1404"))
    assert not app.installed("anno1404") and (game / "maindata/data2.rda").read_bytes() == own
    with pytest.raises(RuntimeError, match="Anno 2205 not found"):
        app.build_archives("anno2205")
    with pytest.raises(RuntimeError, match="absolute path to install"):
        app.build_archives("anno2205", install=True)  # its target folder is a setting of its own


def test_a_port_reads_the_games_in_its_job_not_on_the_callers_thread(tmp_path, monkeypatch):
    """Opening an install's index is seconds of work: it must happen in the background job (the window stays alive)."""
    import threading

    app = editorapp.App(tmp_path / "settings.json")
    app.settings.mods = str(tmp_path / "mods")
    seen = []

    def side(key):
        seen.append(threading.current_thread())
        raise RuntimeError("stop here")

    monkeypatch.setattr(app, "port_side", side)
    job = app.convert_region("anno117", "data/base/provinces/roman/islands/pool/x/x.a7m", "anno117:celtic")
    job._thread.join(10)
    assert seen and seen[0] is not threading.main_thread() and job.state == "failed"
    assert job.lines[0].startswith("opening")


def test_a_log_line_written_while_the_status_is_read_is_not_skipped():
    """The window asks for the lines from ``next`` on: a line the job wrote between the read of the lines and the
    count of them was never shown."""

    class Racing(list):
        def __getitem__(self, key):
            got = super().__getitem__(key)
            if isinstance(key, slice) and len(self) == 1:
                self.append("late")  # the job's thread, between the two reads
            return got

    job = editorapp.Job("ok", lambda job: None)
    job._thread.join(5)
    job.lines = Racing(["first"])
    status = job.status(0)
    assert status["lines"] == ["first"] and job.status(status["next"])["lines"] == ["late"]
