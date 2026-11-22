"""The font table of Anno 1404 and 1701: a ``<Font>`` per name in ``data/loca/<language>/[txt/]localisation.xml``.

``read(paths, read)`` gives (name, family, size, weight) per font. The families are Windows ones (Times New Roman, Arial,
Courier New): the games name them and ship no file for them, only ``GameFont`` (Georgia). Sizes are pixels at the
1024 reference (``RelativeFontSize``, ``Height_1024`` or ``FontSize``). 1701's file is not well-formed XML, so the
fields are read by pattern (docs/ui/old-gui.md, "Fonts").
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

LANGUAGES = ("eng", "english", "ger", "german")
_FILE = re.compile(r"data/loca/([^/]+)/(?:txt/)?localisation(?:_addon)?\.xml")
_FONT = re.compile(r"<Font>(.*?)</Font>", re.S)
_FIELD = re.compile(r"<(\w+)>([^<]*)</\1>")


def number(text, default: float) -> float:
    """``text`` as a number, ``default`` where it is none (the one reader of the old interface's numbers)."""
    try:
        return float(text) if text not in (None, "") else default
    except (TypeError, ValueError):
        return default


def files_of(paths: Iterable[str]) -> list[str]:
    """The localisation files of the first language the game has, the game's own before its add-on's."""
    found = sorted(path for path in paths if _FILE.fullmatch(path))
    for language in LANGUAGES:
        mine = [path for path in found if _FILE.fullmatch(path).group(1) == language]  # type: ignore[union-attr]
        if mine:
            return sorted(mine, key=lambda path: "addon" in path)
    return []


def read(paths: Iterable[str], read_file: Callable[[str], bytes]) -> list[tuple[str, str, float, int]]:
    """(name, family, size, weight) of every font of the localisation files; a later file's font replaces an earlier one."""
    out: dict[str, tuple[str, str, float, int]] = {}
    for path in files_of(paths):
        for block in _FONT.findall(read_file(path).decode("latin-1")):
            fields = {match.group(1): match.group(2).strip() for match in _FIELD.finditer(block)}
            name = fields.get("Name", "")
            family = fields.get("FontName") or fields.get("Name_1024") or ""
            size = number(fields.get("RelativeFontSize") or fields.get("Height_1024") or fields.get("FontSize"), 12.0)
            weight = int(number(fields.get("Weight_1024") or fields.get("Weight"), 0)) or 400
            if name:
                out[name] = (name, family, size, weight)
    return list(out.values())
