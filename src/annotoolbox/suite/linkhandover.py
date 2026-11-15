"""Anno 1800 to the suite, one way: the game's "open in the editor" command starts a batch file in its ``Tools/ToolOne``
folder with the selected object's GUID; the file this module writes there leaves the GUID in a request file, and
``Watcher`` (a thread of the suite's window) turns it into the same ``edit.request`` event Anno 117's link sends, so
the asset editor opens that asset. No helper library and no pipe: 1800 has none. Nothing here changes the game on its own:
``install`` is asked for (a game folder without spaces; never over a ``launch.bat`` that is not ours), ``uninstall``
removes only ours; the suite never offers the game's data reload for 1800. docs/live-link.md, "Anno 1800".
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ..infra.i18n import tr
from . import livelink
from .settings import appdata

GAME = "anno1800"
TOOL_FOLDER = ("Tools", "ToolOne")
BAT_NAME = "launch.bat"
MARK = "rem anno-toolbox live link hand-over"
FOLDER = "live-link"
POLL = 1.0  # seconds between looks at the request file


class UnsuitableFolder(OSError):
    """The game's folder cannot take the hand-over."""


def folder_ok(game_dir: Path) -> bool:
    """The game starts the batch file by an unquoted command line: a space anywhere in its path breaks it."""
    return " " not in str(bat_path(game_dir))


def request_file(data: Path | None = None) -> Path:
    """Where the batch file leaves the GUID (``data``: the suite's application-data folder, for a test)."""
    return (data or appdata()) / FOLDER / f"edit-request-{GAME}.json"


def bat_text(request: Path) -> str:
    """The batch file: the game calls it as ``launch.bat -startGUID <guid>``; it writes the request as JSON, prints
    nothing (the game's child holds both ends of a pipe nobody reads) and returns at once."""
    folder, file = batch_path(request.parent), batch_path(request)
    return (
        f"@echo off\r\n{MARK}\r\n"
        f'if not exist "{folder}" mkdir "{folder}" >nul 2>&1\r\n'
        f'>"{file}" echo {{"game":"{GAME}","guid":%2}}\r\n'
        "exit /b 0\r\n"
    )


def batch_path(path: Path) -> str:
    """``path`` as the ASCII batch file names it: a user folder with other letters (a user named ``Jörg``) through the
    variable that holds it, which cmd expands itself; ``UnsuitableFolder`` when no variable covers it."""
    text = str(path)
    if text.isascii() and "%" not in text:
        return text
    for var in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        base = os.environ.get(var)
        if base and Path(path).is_relative_to(base):
            rest = "\\".join(Path(path).relative_to(base).parts)  # cmd's separator on any host the file is written on
            if rest.isascii() and "%" not in rest:
                return f"%{var}%\\{rest}" if rest else f"%{var}%"
    raise UnsuitableFolder(
        tr("{path} has letters the game's start file cannot hold: set LOCALAPPDATA to a folder without them", path=path)
    )


def bat_path(game_dir: Path) -> Path:
    return Path(game_dir).joinpath(*TOOL_FOLDER, BAT_NAME)


def install(game_dir: Path, data: Path | None = None) -> Path:
    """Write the batch file into the game's ``Tools/ToolOne``; ``FileExistsError`` when another ``launch.bat`` is there
    (the game's own tool start is never replaced), ``UnsuitableFolder`` when the folder's path has a space."""
    path = bat_path(game_dir)
    if not folder_ok(game_dir):
        raise UnsuitableFolder(
            tr(
                "{path} has a space in its name: the game starts the file without quotes and would not find it",
                path=path,
            )
        )
    if path.is_file() and MARK not in path.read_text(encoding="utf-8", errors="replace"):
        raise FileExistsError(tr("{path} is not the suite's: it is left as it is", path=path))
    text = bat_text(request_file(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("ascii"))  # CRLF as written, for cmd.exe
    return path


def uninstall(game_dir: Path) -> bool:
    """Remove the suite's batch file; False when there is none or it is not ours."""
    path = bat_path(game_dir)
    if not path.is_file() or MARK not in path.read_text(encoding="utf-8", errors="replace"):
        return False
    path.unlink()
    return True


def take_request(data: Path | None = None) -> dict | None:
    """The request the game left (read and removed), or None; only a number as the GUID is taken."""
    path = request_file(data)
    try:
        text = path.read_text(encoding="utf-8")
        path.unlink()
        found = json.loads(text)
    except (OSError, ValueError):
        return None
    guid = found.get("guid") if isinstance(found, dict) else None
    if not isinstance(guid, int) or isinstance(guid, bool) or guid <= 0:
        return None
    return {"game": GAME, "guid": guid}


class Watcher:
    """Looks for the request file every ``POLL`` seconds and emits ``edit.request`` on the suite's current link."""

    def __init__(self, data: Path | None = None, interval: float = POLL) -> None:
        self._data, self._interval = data, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="live-link-handover", daemon=True)

    def start(self) -> Watcher:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            request = take_request(self._data)
            if request:
                livelink.current().emit(livelink.EDIT_REQUEST, request)
