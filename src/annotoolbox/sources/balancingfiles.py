"""2070's balancing files and one way to read them: a missing or broken file is said, never skipped silently."""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterator
from xml.etree.ElementTree import ParseError

BASE = "data/config/game/properties.xml"  # the base game's balancing
ADDON = "addondata/config/balancing/addon_01_assets.xml"  # the add-on's, over it
FILES = (BASE, ADDON)
OPTIONAL = "addondata/"  # an install without the add-on has none of these: not worth a warning


def say(warn: Callable[[str], None] | None, message: str) -> None:
    """Report ``message`` through ``warn``, else as a Python warning: never nothing."""
    if warn is not None:
        warn(message)
    else:
        warnings.warn(message, stacklevel=4)


def read_texts(
    tree, files: tuple[str, ...] = FILES, warn: Callable[[str], None] | None = None
) -> Iterator[tuple[str, str]]:
    """(path, text) of each of ``files`` in the install. An add-on file the install lacks is skipped; any other
    file that is missing or unreadable is reported through ``warn`` (default: a Python warning) and skipped."""
    for path in files:
        try:
            yield path, tree.read_text(path)
        except (FileNotFoundError, KeyError):
            if not path.startswith(OPTIONAL):
                say(warn, f"balancing file {path} is not in the install: its values fall back to the defaults")
        except (OSError, ValueError) as exc:
            say(warn, f"balancing file {path} could not be read ({exc}): its values fall back to the defaults")


def load[T](
    tree, parse: Callable[[str], T], files: tuple[str, ...] = FILES, warn: Callable[[str], None] | None = None
) -> Iterator[tuple[str, T]]:
    """(path, ``parse(text)``) of each readable file; a file ``parse`` rejects is reported and skipped."""
    for path, text in read_texts(tree, files, warn):
        try:
            yield path, parse(text)
        except (ValueError, ParseError) as exc:
            say(warn, f"balancing file {path} could not be parsed ({exc}): its values fall back to the defaults")
