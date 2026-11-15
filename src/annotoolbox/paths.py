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


def local_data_dir() -> Path:
    """The tool's own folder for settings, caches and registries: ``anno-toolbox`` under ``%LOCALAPPDATA%``
    (``~/AppData/Local`` on Windows without it, ``~/.local/share`` elsewhere)."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / ("AppData/Local" if os.name == "nt" else ".local/share"))
    return Path(base) / "anno-toolbox"


def documents_dir() -> Path:
    """The user's Documents folder (moved folders included; ``~/Documents`` where Windows cannot say)."""
    try:
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
            return Path(buf.value)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"
