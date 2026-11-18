"""The Toolbox's command line (``suite/toolbox``): the window-less groups, the light install without the GUI libraries,
and what the window says when they are missing."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from tests import rdmsamples

from annotoolbox.suite import toolbox

SRC = Path(__file__).resolve().parents[1] / "src"

LIGHT_SCRIPT = """
import sys
for name in ("PySide6", "moderngl", "numpy", "scipy", "PIL", "miniaudio"):
    sys.modules[name] = None  # an import of any of them now fails, as in an install without them

import annotoolbox.formats.filedb
import annotoolbox.formats.rda
import annotoolbox.formats.rdmread
import annotoolbox.mod.modxml
import annotoolbox.mod.xmlbuild
import annotoolbox.sources.gametree
from annotoolbox.suite import toolbox

assert toolbox.gui_missing(), "the blocked import must be noticed"
code = toolbox.main(sys.argv[1:])
sys.exit(code)
"""


def light(tmp_path, *argv) -> subprocess.CompletedProcess:
    script = tmp_path / "light.py"
    head = f"import sys\nsys.path.insert(0, {str(SRC)!r})  # -I ignores PYTHONPATH\n"
    script.write_text(head + textwrap.dedent(LIGHT_SCRIPT), encoding="utf-8", newline="\n")
    env = {"PYTHONDONTWRITEBYTECODE": "1", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    return subprocess.run(
        [sys.executable, "-I", str(script), *map(str, argv)], capture_output=True, text=True, env=env, check=False
    )


def test_the_rdm_group_runs_without_any_gui_library(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "house.rdm").write_bytes(rdmsamples.static_file())
    done = light(tmp_path, "rdm", "export", tmp_path / "in", "-o", tmp_path / "glb")
    assert done.returncode == 0, done.stdout + done.stderr
    assert (tmp_path / "glb" / "house.glb").is_file()
    back = light(tmp_path, "rdm", "import", tmp_path / "glb", "-o", tmp_path / "back")
    assert back.returncode == 0, back.stdout + back.stderr
    assert (tmp_path / "back" / "house.rdm").read_bytes() == rdmsamples.static_file()
    assert (
        "layout" in light(tmp_path, "rdm", "layouts").stdout.lower()
        or light(tmp_path, "rdm", "layouts").returncode == 0
    )


def test_the_export_group_runs_without_any_gui_library(tmp_path):
    (tmp_path / "in" / "sub").mkdir(parents=True)
    (tmp_path / "in" / "sub" / "data.xml").write_text("<Root><A>1</A></Root>", encoding="utf-8")
    done = light(tmp_path, "export", tmp_path / "in", "-o", tmp_path / "out", "--format", "raw,json")
    assert done.returncode == 0, done.stdout + done.stderr
    assert (tmp_path / "out" / "sub" / "data.xml").is_file()
    assert (tmp_path / "out" / "index.json").is_file()


def test_the_window_names_the_extra_to_install_when_the_gui_libraries_are_missing(tmp_path):
    done = light(tmp_path)
    assert done.returncode == 2
    assert "[gui]" in done.stderr and "pip install" in done.stderr


def test_the_installed_console_program_started_bare_prints_its_help(monkeypatch, capsys):
    assert toolbox.console_program("C:/x/anno-toolbox.exe", frozen=True)
    assert not toolbox.console_program("C:/x/AnnoToolbox.exe", frozen=True)  # the window program opens the window
    assert not toolbox.console_program("/opt/x/anno-toolbox", frozen=False)  # a source checkout's command too
    monkeypatch.setattr(toolbox, "console_program", lambda: True)
    monkeypatch.setattr(toolbox, "run_window", lambda argv: pytest.fail("a bare console start opened the window"))
    assert toolbox.main([]) == 0 and "rdm" in capsys.readouterr().out


def test_the_window_programs_name_is_the_installers():
    from annotoolbox.paths import REPO_ROOT

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import installer_bundle

    assert installer_bundle.PROGRAM == toolbox.WINDOW_PROGRAM


def test_the_groups_are_the_toolboxs_sub_commands(capsys):
    parser = toolbox.group_parser()
    args = parser.parse_args(["rdm", "validate", "model.rdm"])
    assert args.command == "rdm" and args.rdm_command == "validate"
    assert parser.parse_args(["export", "a", "-o", "out"]).command == "export"
    with pytest.raises(SystemExit):
        parser.parse_args(["rdm"])
    # the commands the guides name (docs/rda-format.md, save-format.md, references.md, viewer.md, rdm-gltf.md)
    for argv in (["rda", "list", "a.rda"], ["save", "show", "a.a8s"], ["unreferenced", "anno117"],
                 ["viewer-files", "anno117"], ["rdm", "granny", "m.glb"]):  # fmt: skip
        assert parser.parse_args(argv).func.__name__.startswith("cmd_")
        assert argv[0] in toolbox.GROUPS


def test_the_rda_group_runs_without_any_gui_library(tmp_path):
    (tmp_path / "in" / "data").mkdir(parents=True)
    (tmp_path / "in" / "data" / "a.txt").write_text("hello", encoding="utf-8")
    packed = light(tmp_path, "rda", "pack", tmp_path / "in", tmp_path / "a.rda", "--version", "2.2")
    assert packed.returncode == 0, packed.stdout + packed.stderr
    listed = light(tmp_path, "rda", "list", tmp_path / "a.rda")
    assert listed.returncode == 0 and "data/a.txt" in listed.stdout


def test_a_command_that_needs_a_library_the_light_install_lacks_names_it_and_the_extra(tmp_path):
    done = light(tmp_path, "island", "port", "anno117", "x.a7m", "--to", "anno117:roman", "--out", tmp_path / "out")
    assert done.returncode == 1 and done.stderr.count("\n") == 1, done.stderr
    assert "island needs numpy" in done.stderr and "pip install numpy" in done.stderr and "[gui]" in done.stderr
    assert toolbox.missing_library("rdm", ModuleNotFoundError("x", name="PIL.Image")).startswith("rdm needs Pillow")


def test_an_uncaught_error_of_the_windowed_program_is_logged_and_shown(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    shown: list = []
    toolbox.report_errors(tmp_path / "logs", show=lambda text, log: shown.append((text, log)))
    try:
        raise KeyError("lost")
    except KeyError:
        sys.excepthook(*sys.exc_info())
    ((text, log),) = shown
    assert text == "KeyError: 'lost'" and log.parent == tmp_path / "logs" and "KeyError" in log.read_text("utf-8")
    worker = threading.Thread(target=lambda: 1 / 0)
    worker.start()
    worker.join()
    assert len(list((tmp_path / "logs").iterdir())) == 2 and len(shown) == 1  # a thread's error: logged only


def test_the_programs_log_lines_go_into_a_capped_file_and_old_error_reports_go(tmp_path, monkeypatch):
    import logging

    logs = tmp_path / "logs"
    logs.mkdir()
    for n in range(5):
        (logs / f"error-2026111{n}-000000-000000.log").write_text("x", encoding="utf-8")
    monkeypatch.setattr(toolbox, "LOG_BYTES", 2000)
    monkeypatch.setattr(toolbox, "ERRORS_KEPT", 2)
    ours, theirs = logging.getLogger("annotoolbox.suite.linkclient"), logging.getLogger("some.library")
    try:
        handler = toolbox.start_log(logs)
        assert toolbox.start_log(logs) is handler and logging.getLogger().handlers.count(handler) == 1
        ours.info("live link lost: %s", "closed")
        theirs.info("chatter")
        theirs.warning("a library's warning")
        text = (logs / toolbox.LOG_FILE).read_text("utf-8")
        assert "INFO annotoolbox.suite.linkclient: live link lost: closed" in text
        assert "a library's warning" in text and "chatter" not in text
        for n in range(200):
            ours.warning("line %03d of a long run", n)
    finally:
        toolbox.stop_log()
    kept = sorted(p.name for p in logs.iterdir())
    assert kept == [f"error-2026111{n}-000000-000000.log" for n in (3, 4)] + [
        toolbox.LOG_FILE, f"{toolbox.LOG_FILE}.1", f"{toolbox.LOG_FILE}.2"
    ]  # fmt: skip
    assert all((logs / name).stat().st_size <= 2000 for name in kept)
    assert "line 199" in (logs / toolbox.LOG_FILE).read_text("utf-8")  # the newest lines are the ones kept
    assert toolbox.start_log(tmp_path / "file" / "x") is not None  # a new folder is made
    toolbox.stop_log()
    (tmp_path / "taken").write_text("", encoding="utf-8")
    assert toolbox.start_log(tmp_path / "taken") is None  # no folder to write: the program starts without a log


def test_an_error_is_one_line_and_exit_one(tmp_path, capsys):
    assert toolbox.main(["rdm", "inspect", str(tmp_path / "missing.rdm")]) == 1
    assert capsys.readouterr().err.startswith("error:")


def test_two_errors_in_the_same_clock_step_keep_two_logs(tmp_path, monkeypatch):
    from datetime import datetime

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 15, 20, 0, 0, 0)

    monkeypatch.setattr("datetime.datetime", Frozen)
    try:
        raise KeyError("a")
    except KeyError:
        first = toolbox.write_error_log(tmp_path, *sys.exc_info())
        second = toolbox.write_error_log(tmp_path, *sys.exc_info())
    assert first != second and first.is_file() and second.is_file()
