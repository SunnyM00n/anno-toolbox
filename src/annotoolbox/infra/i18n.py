"""The Toolbox's interface language: ``tr`` marks and looks up a string, ``trn`` a count, ``N_`` marks one that is
translated later, ``number`` formats a number. Pure Python, no Qt, so the apps and the command line share it.

``use(code)`` selects the language (the suite settings' ``language``; ``ANNO_TOOLBOX_LANG`` overrides it). A language
is one file, ``infra/lang/<code>.json``: sections by app, each a sorted list of source text -> translation. The design
is in ``docs/tool-suite.md`` section 25; ``scripts/check_i18n.py`` compares the files with the code and with English.
"""

from __future__ import annotations

import json
import os
import string
from collections.abc import Sequence
from pathlib import Path

DEFAULT = "en"
LANG_DIR = Path(__file__).with_name("lang")
ENV_VAR = "ANNO_TOOLBOX_LANG"
META = "meta"  # the section of a language file that holds its own name ({"name": "Deutsch"}), not strings

_active = DEFAULT
_catalog: dict[str, str | list[str]] = {}
_missing: set[str] = set()


def catalog_key(text: str, context: str = "") -> str:
    """The key a catalog files a source text under: the text itself, ``context|text`` when a context tells apart."""
    return f"{context}|{text}" if context else text


def read_file(code: str) -> dict[str, dict]:
    """The sections of a language's file (``META`` and one per app); an empty dict when the language has none."""
    path = LANG_DIR / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def load_catalog(code: str) -> dict[str, str | list[str]]:
    """A language's strings, the sections flattened (a text in two sections has one translation: ``check_i18n``)."""
    merged: dict[str, str | list[str]] = {}
    for section, entries in read_file(code).items():
        if section != META:
            merged.update(entries)
    return merged


def languages() -> dict[str, str]:
    """The languages found, code -> its own name (a file's ``meta``); the default first, the rest by code."""
    found = {DEFAULT: read_file(DEFAULT).get(META, {}).get("name", "English")}
    for path in sorted(LANG_DIR.glob("*.json")):
        if path.stem != DEFAULT:
            found[path.stem] = read_file(path.stem).get(META, {}).get("name", path.stem)
    return found


def dump_file(sections: dict[str, dict]) -> str:
    """A language file as text: ``meta`` first, then the sections by name, one sorted entry per line (UTF-8, LF)."""
    names = [META, *sorted(s for s in sections if s != META)] if META in sections else sorted(sections)
    out = []
    for name in names:
        entries = sections[name]
        lines = [
            f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(entries[k], ensure_ascii=False)}"
            for k in sorted(entries)
        ]
        out.append(f"  {json.dumps(name)}: {{\n" + ",\n".join(lines) + "\n  }")
    return "{\n" + ",\n".join(out) + "\n}\n"


def use(code: str | None = None) -> str:
    """Select the interface language (``None``: the environment's, else the default); returns the code in effect."""
    global _active, _catalog
    code = (os.environ.get(ENV_VAR) or code or DEFAULT).strip().lower()
    if code not in languages():
        code = DEFAULT
    _active = code
    _catalog = {} if code == DEFAULT else load_catalog(code)
    _missing.clear()
    return code


def current() -> str:
    return _active


def missing() -> frozenset[str]:
    """Keys looked up in the active language that its catalog lacks (shown in English); for tests and checks."""
    return frozenset(_missing)


def _entry(key: str, text: str) -> str | list[str]:
    if _active == DEFAULT:
        return text
    found = _catalog.get(key)
    if found is None:
        _missing.add(key)
        return text
    return found


def tr(text: str, /, *, context: str = "", **fields: object) -> str:
    """The text in the interface language; ``fields`` fill its ``{name}`` places (without fields the text is as is)."""
    found = _entry(catalog_key(text, context), text)
    shown = found if isinstance(found, str) else found[0]
    return shown.format(**fields) if fields else shown


def trn(one: str, other: str, n: int, /, *, context: str = "", **fields: object) -> str:
    """``one`` for a count of 1, else ``other``, in the interface language; ``{n}`` is the count. The catalog entry
    of ``one`` is a list ``[singular, plural]`` (German and English share the rule)."""
    found = _entry(catalog_key(one, context), one if n == 1 else other)
    if isinstance(found, list):
        found = found[0] if n == 1 else found[1]
    return found.format(n=n, **fields)


def alternatives(items: Sequence[str]) -> str:
    """``items`` as one phrase of choices (``a or b or c``), in the interface language's word and word order."""
    joined = ""
    for item in items:
        joined = tr("{a} or {b}", a=joined, b=item) if joined else item
    return joined


def N_(text: str) -> str:
    """Mark a string that is translated where it is shown (``tr(variable)``), for tables built at import time."""
    return text


def places(text: str) -> set[str]:
    """The ``{name}`` places of a format string (what a translation must keep)."""
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def number(value: float, decimals: int = 0) -> str:
    """A number as the interface language writes it: 1,234.5 in English, 1.234,5 in German."""
    text = f"{value:,.{decimals}f}"
    if _active == "de":
        text = text.translate({ord(","): ".", ord("."): ","})
    return text
