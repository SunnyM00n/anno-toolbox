"""Where the package and the checkout are, for the code that reads files beside the package.

The checkout's folders (``data/``, ``docs/formats/``, ``tools/``, ``island-edits/``) are found from here; an
installed copy without a checkout falls back to the user's data folder where a module says so.
"""

import os
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
# The data files the readers load (schemas, tables): generated into the checkout's docs/formats/, and put inside the
# package by the wheel's build (pyproject.toml, ``force-include``) as ``<package>/formats/data/<name>``.
FORMAT_DATA = ("phoenix-schema.json", "phoenix-schema-1800.json", "enums-117.json")
FORMAT_DATA_DIR = "formats/data"


def format_data(name: str) -> Traversable:
    """The data file ``name`` (one of ``FORMAT_DATA``): the installed package's copy, else the checkout's
    ``docs/formats/`` (a source tree, the program folder). Read it with ``read_text`` or ``open``."""
    shipped = resources.files(__package__ or __name__.split(".")[0]).joinpath(*FORMAT_DATA_DIR.split("/"), name)
    return shipped if shipped.is_file() else REPO_ROOT / "docs" / "formats" / name


APP_FOLDER = "anno-toolbox"


def _xdg(variable: str, fallback: str) -> Path:
    """An XDG base folder: the variable when it holds an absolute path (the spec ignores a relative one), else
    ``~/<fallback>``."""
    value = os.environ.get(variable, "")
    return Path(value) if value and os.path.isabs(value) else Path.home() / fallback


def local_data_dir() -> Path:
    """The tool's own folder for registries, logs and install state: ``anno-toolbox`` under ``%LOCALAPPDATA%``
    (``~/AppData/Local`` on Windows without it); on Linux ``$XDG_DATA_HOME`` (``~/.local/share``)."""
    if os.name != "nt":
        return _xdg("XDG_DATA_HOME", ".local/share") / APP_FOLDER
    base = os.environ.get("LOCALAPPDATA")
    return Path(base or Path.home() / "AppData/Local") / APP_FOLDER


def config_dir() -> Path:
    """Where the settings file lives: the data folder on Windows, ``$XDG_CONFIG_HOME`` (``~/.config``) on Linux."""
    if os.name != "nt":
        return _xdg("XDG_CONFIG_HOME", ".config") / APP_FOLDER
    return local_data_dir()


def cache_dir() -> Path:
    """The regenerable build cache: ``cache`` in the data folder on Windows, ``$XDG_CACHE_HOME`` (``~/.cache``) on
    Linux."""
    if os.name != "nt":
        return _xdg("XDG_CACHE_HOME", ".cache") / APP_FOLDER
    return local_data_dir() / "cache"


def _xdg_user_dir(name: str) -> Path | None:
    """``XDG_<name>_DIR`` from ``user-dirs.dirs`` in the config folder (``$HOME`` in it expanded), None if absent."""
    try:
        text = (_xdg("XDG_CONFIG_HOME", ".config") / "user-dirs.dirs").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.strip().partition("=")
        if key == f"XDG_{name}_DIR" and value:
            path = value.strip().strip('"').replace("$HOME", str(Path.home()))
            return Path(path) if os.path.isabs(path) else None
    return None


def documents_dir() -> Path:
    """The user's Documents folder (moved folders included; ``~/Documents`` where the system cannot say; on Linux
    ``XDG_DOCUMENTS_DIR`` of ``user-dirs.dirs``)."""
    if os.name != "nt":
        return _xdg_user_dir("DOCUMENTS") or Path.home() / "Documents"
    try:
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
            return Path(buf.value)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"
