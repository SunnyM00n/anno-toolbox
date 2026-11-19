"""The Toolbox's one command line: the commands that need no window, and the window.

The window-less ``GROUPS`` (``rdm``, ``export``, ``rda`` on the core alone; ``island`` needs numpy) run on a light
install (no PySide6). Anything else starts the window: the island editor, or with ``--app`` the suite's window; that
needs the ``gui`` extra, and without it ``main`` says so instead of failing in an import. ``main`` is the entry point
of the installed command and of ``python -m <package>``. A windowed program has no console: ``report_errors`` and
``start_log`` put its errors and its log lines into ``<appdata>/logs``. docs/tool-suite.md.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .. import __version__
from ..infra.i18n import tr

PROG = "anno-toolbox"  # the installed command's name (the public edition renames it)
WINDOW_PROGRAM = (
    "AnnoToolbox"  # the installed program's name (scripts/installer_bundle.PROGRAM): AnnoToolbox.exe, AnnoToolbox.com
)
GUI_EXTRA = "gui"
GROUPS = (
    "rdm",
    "export",
    "rda",
    "island",
    "save",
    "viewer-files",
    "unreferenced",
    "texture",
    "sound",
    "mod",
)  # no window
PIP_NAMES = {"PIL": "Pillow"}  # an import name that is not the name pip installs
LOG_FILE = "toolbox.log"  # in ``<appdata>/logs``: what the program's modules log
LOG_BYTES = 512 * 1024  # a log file's size before the next is begun
LOG_KEPT = 2  # older log files kept beside it (toolbox.log.1, .2)
ERRORS_KEPT = 20  # the newest error reports (``write_error_log``) that stay
_log_handler: logging.Handler | None = None


def prog_name(frozen: bool | None = None) -> str:
    """The name the help prints: the installed program's own (``AnnoToolbox``, what a terminal runs), else ``PROG``."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    return WINDOW_PROGRAM if frozen else PROG


def gui_missing() -> str:
    """ "" when the window's libraries are installed, else the message that says what to install."""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return f'The window needs the GUI libraries: pip install -e ".[{GUI_EXTRA}]" in the checkout (rdm and export need none).'
    return ""


def group_parser() -> argparse.ArgumentParser:
    """The parser of the window-less commands (``GROUPS``): ``rdm <command>``, ``export``, ``rda <command>``,
    ``island <command>``, ``save <command>``, ``viewer-files``, ``unreferenced``, ``texture bc7`` and ``sound wem``."""
    from ..assetedit import handovercommands
    from ..converterapp import commands as format_commands
    from ..editor import islandcommands
    from ..exporting import commands as export_commands
    from ..exporting import rdacommands
    from ..rdm import commands as rdm_commands
    from ..refgraph import commands as ref_commands
    from ..saveedit import commands as save_commands
    from ..viewer import filecommands
    from . import importcommands

    p = argparse.ArgumentParser(
        prog=prog_name(), description="The Toolbox: models, textures, sounds and files of the Anno games."
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True, metavar="{" + ",".join(GROUPS) + "}")
    rdm = sub.add_parser(
        "rdm", help="RDM models: export to glTF, import, inspect, diff, validate, relayout, layouts, Granny .gr2"
    )
    rdm_commands.register(rdm.add_subparsers(dest="rdm_command", required=True))
    export_commands.register(sub)
    rdacommands.register(sub)
    island = sub.add_parser("island", help="islands of 117 and 1800: port to another region or game, list regions")
    islandcommands.register(island.add_subparsers(dest="island_command", required=True))
    save_commands.register(sub)
    handovercommands.add_handover(importcommands.register(sub))
    filecommands.register(sub)
    ref_commands.register(sub)
    format_commands.register(sub)
    return p


def game_keys(text: str) -> tuple[str, ...]:
    """``--games``: a comma list of the keys of ``installs.GAMES``, any of them."""
    from ..othergames.installs import GAMES

    keys = tuple(dict.fromkeys(k.strip() for k in text.split(",") if k.strip()))
    if not keys or any(k not in GAMES for k in keys):
        raise argparse.ArgumentTypeError(f"{text!r}: a comma list of {', '.join(GAMES)}")
    return keys


def window_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=prog_name(), description="The window: the island editor, or with --app the suite."
    )
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("island", nargs="?", help="an island file (a game path) to open at once")
    ap.add_argument("--mod", type=Path, help="the mod folder the island file is in")
    ap.add_argument("--edits", type=Path, help="the edit file to use")
    ap.add_argument(
        "--games", type=game_keys, metavar="KEYS", help="restrict the editor to these games (anno117,anno1800,...)"
    )
    ap.add_argument("--app", metavar="APP", help="the suite's window, on this app (islands, assets, viewer, ...)")
    ap.add_argument("--smoke", action="store_true", help="with --app: open every app once and close")
    return ap


def run_group(argv: list[str]) -> int:
    """A window-less command: one line on stderr and exit 1 for an error a user can act on."""
    args = group_parser().parse_args(argv)
    try:
        return args.func(args)
    except ImportError as exc:  # a light install: name what to install
        print(f"error: {missing_library(argv[0], exc)}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"error: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}", file=sys.stderr)
        return 1


def missing_library(command: str, exc: ImportError) -> str:
    """What a command that needs a library the install lacks says: the library and how to get it."""
    module = (exc.name or "").split(".")[0]
    if not module:
        return str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    package = PIP_NAMES.get(module, module)
    return (
        f"{command} needs {package}, which is not installed: pip install {package}"
        f' (or the full Toolbox, in the checkout: pip install -e ".[{GUI_EXTRA}]")'
    )


def report_errors(logs: Path | None = None, show=None) -> None:
    """An error nobody caught goes into ``<appdata>/logs`` and into a message box (``show(text, log)``; the default
    needs a window open, on the main thread): the windowed program has no console, so without this it would vanish.
    A thread's error is only logged. ``logs``: the folder (a test's)."""
    import threading
    import traceback

    show = show or _show_error

    def hook(kind, value, tb) -> None:
        path = write_error_log(logs, kind, value, tb)
        if sys.stderr is not None:
            traceback.print_exception(kind, value, tb)
        show(f"{kind.__name__}: {value}", path)

    def thread_hook(args) -> None:
        write_error_log(logs, args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = hook
    threading.excepthook = thread_hook


def write_error_log(logs: Path | None, kind, value, tb) -> Path | None:
    """The traceback in a file of its own in ``logs`` (else ``<appdata>/logs``); None when it cannot be written."""
    import traceback
    from datetime import datetime

    from .settings import appdata

    folder = logs or appdata() / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        stamp = f"error-{datetime.now():%Y%m%d-%H%M%S-%f}"
        text = "".join(traceback.format_exception(kind, value, tb))
        for n in range(1, 100):  # Windows' clock steps coarsely: two errors at once must not share a file
            path = folder / (f"{stamp}.log" if n == 1 else f"{stamp}-{n}.log")
            try:
                with path.open("x", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                return path
            except FileExistsError:
                continue
        return None
    except OSError:
        return None


def start_log(logs: Path | None = None) -> logging.Handler | None:
    """The program's log lines (a lost live link, an update that failed, an installer event) into ``LOG_FILE`` in
    ``logs`` (else ``<appdata>/logs``): our modules from INFO, the libraries from WARNING, ``LOG_BYTES`` a file and
    ``LOG_KEPT`` older ones; the oldest error reports go. Once per program; None when the folder cannot be written."""
    from logging.handlers import RotatingFileHandler

    from .settings import appdata

    global _log_handler
    if _log_handler is not None:
        return _log_handler
    folder = logs or appdata() / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / LOG_FILE, maxBytes=LOG_BYTES, backupCount=LOG_KEPT, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger(__name__.split(".")[0]).setLevel(logging.INFO)
    prune_error_logs(folder, ERRORS_KEPT)
    _log_handler = handler
    return handler


def stop_log() -> None:
    """Close the log file: before the uninstaller moves the data folder away (an open file would hold it), and
    in a test. The program keeps it to its end otherwise."""
    global _log_handler
    if _log_handler is not None:
        logging.getLogger().removeHandler(_log_handler)
        logging.getLogger(__name__.split(".")[0]).setLevel(logging.NOTSET)
        _log_handler.close()
        _log_handler = None


def prune_error_logs(folder: Path, keep: int) -> list[Path]:
    """Remove all but the ``keep`` newest error reports in ``folder`` (their names sort by time); returns the removed."""
    removed = []
    try:
        old = sorted(folder.glob("error-*.log"))[: -keep or None]
    except OSError:
        return removed
    for path in old:
        try:
            path.unlink()
            removed.append(path)
        except OSError:  # open in an editor: it goes at a later start
            continue
    return removed


def _show_error(text: str, path: Path | None) -> None:
    import threading

    if "PySide6" not in sys.modules or threading.current_thread() is not threading.main_thread():
        return
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import splash
    from .shell import TITLE

    splash.close()  # a box behind the start-up screen would never be seen
    if QApplication.instance() is not None:
        where = "\n\n" + tr("assets.details_path", path=path) if path else ""
        QMessageBox.critical(None, TITLE, tr("assets.something_went_wrong", error=text) + where)


def run_window(argv: list[str]) -> int:
    args = window_parser().parse_args(argv)
    if message := gui_missing():
        print(message, file=sys.stderr)
        return 2
    if getattr(sys, "frozen", False) or sys.stderr is None:  # the installed program, or no console to print to
        report_errors(show=(lambda _text, _log: None) if args.smoke else None)  # a smoke start never waits on a box
        start_log()
    from . import splash

    if not args.smoke:
        splash.show()  # before the heavy imports below: the person sees the program start at once
    try:
        return start_window(args)
    finally:
        splash.close()  # it closes itself when the first window shows; this is the error and early-exit way


def default_app(args: argparse.Namespace, frozen: bool) -> str | None:
    """The suite's app to open: ``--app``'s, else (the installed program, started from a shortcut with no arguments)
    the island editor's page in the suite's window; None for the island editor alone."""
    if args.app:
        return args.app
    plain = not (args.island or args.mod or args.edits or args.games)
    return "islands" if frozen and plain else None


def start_window(args: argparse.Namespace) -> int:
    from . import splash

    splash.status(tr("common.loading_apps"))
    app = default_app(args, bool(getattr(sys, "frozen", False)))
    if app:
        from . import apps, shell

        return shell.run(
            apps.available(), start=app, parts=apps.parts(), deliveries=apps.deliveries(), smoke=args.smoke
        )
    from ..editor.desktop.workbench import run

    return run(args.island, args.mod, args.edits, args.games)


def console_program(executable: str | None = None, frozen: bool | None = None) -> bool:
    """Whether this is the installed console program: started bare it prints its help, the window program (and a
    source checkout's command) opens the window. On Windows the installed program is two files of one name,
    ``AnnoToolbox.exe`` (the window) and ``AnnoToolbox.com`` (the console build), told apart by the suffix; on Linux
    there is one program, which opens the window."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    return frozen and Path(executable or sys.executable).suffix.lower() == ".com"


def main(argv: list[str] | None = None) -> int:
    from . import installevents

    installevents.run()  # the installer's start-up calls first: before Qt and the arguments (it exits for one of them)
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] and argv[0] in GROUPS:
        return run_group(argv)
    if argv[:1] in (["-h"], ["--help"]) or (not argv and console_program()):
        print(group_parser().format_help())
        print(window_parser().format_help())
        return 0
    return run_window(argv)


if __name__ == "__main__":
    sys.exit(main())
