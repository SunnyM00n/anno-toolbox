"""The Toolbox's one command line: the commands that need no window, and the window.

The window-less ``GROUPS`` (``rdm``, ``export``, ``rda`` on the core alone; ``island`` needs numpy) run on a light
install (no PySide6). Anything else starts the window: the island editor, or with ``--app`` the suite's window; that
needs the ``gui`` extra, and without it ``main`` says so instead of failing in an import. ``main`` is the entry point
of the installed command and of ``python -m <package>``. docs/tool-suite.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import __version__

PROG = "anno-toolbox"  # the installed command's name (the public edition renames it)
GUI_EXTRA = "gui"
GROUPS = ("rdm", "export", "rda", "island", "save", "viewer-files", "unreferenced")  # the window-less commands
PIP_NAMES = {"PIL": "Pillow"}  # an import name that is not the name pip installs


def gui_missing() -> str:
    """ "" when the window's libraries are installed, else the message that says what to install."""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return f'The window needs the GUI libraries: pip install "{PROG}[{GUI_EXTRA}]" (the rdm and export commands need none).'
    return ""


def group_parser() -> argparse.ArgumentParser:
    """The parser of the window-less commands (``GROUPS``): ``rdm <command>``, ``export``, ``rda <command>``,
    ``island <command>``, ``save <command>``, ``viewer-files`` and ``unreferenced``."""
    from ..editor import islandcommands
    from ..exporting import commands as export_commands
    from ..exporting import rdacommands
    from ..rdm import commands as rdm_commands
    from ..refgraph import commands as ref_commands
    from ..saveedit import commands as save_commands
    from ..viewer import filecommands

    p = argparse.ArgumentParser(
        prog=PROG, description="The Toolbox: models, textures, sounds and files of the Anno games."
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
    filecommands.register(sub)
    ref_commands.register(sub)
    return p


def game_keys(text: str) -> tuple[str, ...]:
    """``--games``: a comma list of the keys of ``installs.GAMES``, any of them."""
    from ..othergames.installs import GAMES

    keys = tuple(dict.fromkeys(k.strip() for k in text.split(",") if k.strip()))
    if not keys or any(k not in GAMES for k in keys):
        raise argparse.ArgumentTypeError(f"{text!r}: a comma list of {', '.join(GAMES)}")
    return keys


def window_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=PROG, description="The window: the island editor, or with --app the suite.")
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
        f' (or the full Toolbox: pip install "{PROG}[{GUI_EXTRA}]")'
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
        path = folder / f"error-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
        path.write_text("".join(traceback.format_exception(kind, value, tb)), encoding="utf-8", newline="\n")
        return path
    except OSError:
        return None


def _show_error(text: str, path: Path | None) -> None:
    import threading

    if "PySide6" not in sys.modules or threading.current_thread() is not threading.main_thread():
        return
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ..infra.i18n import tr
    from .shell import TITLE

    if QApplication.instance() is not None:
        where = "\n\n" + tr("The details are in {path}.", path=path) if path else ""
        QMessageBox.critical(None, TITLE, tr("Something went wrong: {error}", error=text) + where)


def run_window(argv: list[str]) -> int:
    args = window_parser().parse_args(argv)
    if message := gui_missing():
        print(message, file=sys.stderr)
        return 2
    if getattr(sys, "frozen", False) or sys.stderr is None:  # the installed program, or no console to print to
        report_errors(show=(lambda _text, _log: None) if args.smoke else None)  # a smoke start never waits on a box
    if args.app:
        from . import apps, shell

        return shell.run(
            apps.available(), start=args.app, parts=apps.parts(), deliveries=apps.deliveries(), smoke=args.smoke
        )
    from ..editor.desktop.workbench import run

    return run(args.island, args.mod, args.edits, args.games)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] and argv[0] in GROUPS:
        return run_group(argv)
    if argv[:1] in (["-h"], ["--help"]):
        print(group_parser().format_help())
        print(window_parser().format_help())
        return 0
    return run_window(argv)


if __name__ == "__main__":
    sys.exit(main())
