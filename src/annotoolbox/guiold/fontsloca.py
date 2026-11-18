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


def _number(text: str | None, default: float) -> float:
    try:
        return float(text) if text else default
    except ValueError:
        return default


def files_of(paths: Iterable[str]) -> list[str]:
    """The localisation files of the first language the game has, the game's own before its add-on's."""
    found = sorted(p for p in paths if _FILE.fullmatch(p))
    for language in LANGUAGES:
        mine = [p for p in found if _FILE.fullmatch(p).group(1) == language]  # type: ignore[union-attr]
        if mine:
            return sorted(mine, key=lambda p: "addon" in p)
    return []


def read(paths: Iterable[str], read_file: Callable[[str], bytes]) -> list[tuple[str, str, float, int]]:
    """(name, family, size, weight) of every font of the localisation files; a later file's font replaces an earlier one."""
    out: dict[str, tuple[str, str, float, int]] = {}
    for path in files_of(paths):
        for block in _FONT.findall(read_file(path).decode("latin-1")):
            f = {m.group(1): m.group(2).strip() for m in _FIELD.finditer(block)}
            name = f.get("Name", "")
            family = f.get("FontName") or f.get("Name_1024") or ""
            size = _number(f.get("RelativeFontSize") or f.get("Height_1024") or f.get("FontSize"), 12.0)
            weight = int(_number(f.get("Weight_1024") or f.get("Weight"), 0)) or 400
            if name:
                out[name] = (name, family, size, weight)
    return list(out.values())
