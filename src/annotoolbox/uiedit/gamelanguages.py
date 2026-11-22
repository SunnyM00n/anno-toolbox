"""The game's languages for the preview's texts: the ones the install ships text tables for, the one the preview shows
(the Preview settings' choice, else English), how the font assets name a language, and the language a recording
of live values was taken in.

``installed(tree)`` lists them; ``chosen(defaults, available)`` is the preview's; ``same(a, b)`` compares a text
table's name with a font exception's. docs/ui-editor.md "The language of the game's texts".
"""

from __future__ import annotations

import re

FOLDERS = ("data/config/gui/", "data/base/config/gui/")  # 117's text tables, 1800's
_TABLE = re.compile(r"texts_([a-z_]+)\.xml$")
NOT_LANGUAGES = {"metadata"}
DEFAULT = "english"
KNOWN = ("english", "german", "french", "italian", "spanish", "polish", "russian", "brazilian", "japanese", "korean",
         "simplified_chinese", "traditional_chinese")  # 117's text tables (docs/engine-texts.md)  # fmt: skip
# names the font assets use for a language besides its table's name (an older one for the same script)
ALIASES = {"chinese": "simplifiedchinese", "taiwanese": "traditionalchinese", "brazilian": "brazilian"}


def installed(tree) -> list[str]:
    """The languages the install has text tables for (``english``, ``simplified_chinese`` ...), English first."""
    found: set[str] = set()
    for folder in FOLDERS:
        try:
            paths = list(tree.under(folder))
        except (OSError, KeyError, AttributeError):
            continue
        for path in paths:
            match = _TABLE.search(path.lower())
            if match and match.group(1) not in NOT_LANGUAGES:
                found.add(match.group(1))
    return sorted(found, key=lambda language: (language != DEFAULT, language))


def chosen(defaults, available: list[str] | None = None) -> str:
    """The language the preview takes the game's texts from: the Preview settings' choice where the install has it,
    else English (the game keeps the player's own choice with the account, which the Toolbox does not read)."""
    wanted = str(getattr(defaults, "language", "") or "")
    if wanted and (not available or wanted in available):
        return wanted
    return DEFAULT


def _flat(name: str) -> str:
    low = re.sub(r"[^a-z]", "", str(name).lower())
    return ALIASES.get(low, low)


def same(table_name: str, font_name: str) -> bool:
    """Whether a text table's language (``simplified_chinese``) is the one a font exception names (``Simplified
    Chinese``, or an older name of it such as ``Chinese``)."""
    return bool(table_name) and _flat(table_name) == _flat(font_name)


def title(name: str) -> str:
    """A language as shown: ``simplified_chinese`` -> ``Simplified Chinese`` (the game's own words)."""
    return " ".join(part.capitalize() for part in name.split("_"))


def live_note(held, shown: str) -> str:
    """The language live values were taken in when it is not the one shown (``liverecord.SHeld.language`` where the
    recording carries one), else ""."""
    taken = str(getattr(held, "language", "") or "") if held is not None else ""
    return title(taken) if taken and _flat(taken) != _flat(shown) else ""
