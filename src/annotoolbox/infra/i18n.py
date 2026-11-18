"""The Toolbox's interface language: ``tr`` shows the text of a string id, ``trn`` a count's, ``N_`` marks an id that
is shown later, ``number`` formats a number. Pure Python, no Qt, so the apps and the command line share it.

``use(code)`` selects the language (the suite settings' ``language``; ``ANNO_TOOLBOX_LANG`` overrides it). A language
is one file, ``infra/lang/<code>.json``: sections by app, each a sorted list of id -> text; an id a language lacks is
shown in English. The design is in ``docs/tool-suite.md`` section 25; ``scripts/check_i18n.py`` compares the files
with the code and with each other.
"""

from __future__ import annotations

import json
import os
import re
import string
from collections.abc import Sequence
from pathlib import Path

DEFAULT = "en"
LANG_DIR = Path(__file__).with_name("lang")
ENV_VAR = "ANNO_TOOLBOX_LANG"
META = "meta"  # the section of a language file that holds its own name ({"name": "Deutsch"}), not strings
ID = re.compile(r"[a-z][a-z0-9]*(\.[a-z0-9_]+)+")  # <section>.<name>, or <section>.<group>.<name>

Entry = str | list[str]
_active = DEFAULT
_catalog: dict[str, Entry] | None = None  # the active language's strings, read at the first lookup
_english: dict[str, Entry] = {}  # the default language's, shown for an id the active one lacks
_missing: set[str] = set()


def is_id(text: str) -> bool:
    """Whether a text has the shape of a string id (``saves.emperor``, ``islands.menu.file``)."""
    return ID.fullmatch(text) is not None


def section_of(ident: str) -> str:
    """The section of a language file an id is filed in: its first part."""
    return ident.split(".", 1)[0]


def read_file(code: str) -> dict[str, dict]:
    """The sections of a language's file (``META`` and one per app); an empty dict when the language has none."""
    path = LANG_DIR / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def load_catalog(code: str) -> dict[str, Entry]:
    """A language's strings, id -> text (a count's is ``[singular, plural]``), the sections flattened."""
    merged: dict[str, Entry] = {}
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


def _load(code: str) -> None:
    global _active, _catalog, _english
    _english = load_catalog(DEFAULT)
    _catalog = _english if code == DEFAULT else load_catalog(code)
    _active = code
    _missing.clear()


def use(code: str | None = None) -> str:
    """Select the interface language (``None``: the environment's, else the default); returns the code in effect."""
    code = (os.environ.get(ENV_VAR) or code or DEFAULT).strip().lower()
    if code not in languages():
        code = DEFAULT
    _load(code)
    return code


def current() -> str:
    return _active


def missing() -> frozenset[str]:
    """What was looked up without an entry in the active language (shown in English, or as it is when English has
    none either); for tests and checks."""
    return frozenset(_missing)


def _entry(ident: str) -> Entry:
    """An id's entry in the active language, else English's, else the id itself (a text that is no id passes)."""
    if _catalog is None:
        _load(_active)
    found = _catalog.get(ident)
    if found is None:
        _missing.add(ident)
        found = _english.get(ident, ident)
    return found


def tr(ident: str, /, **fields: object) -> str:
    """The text of a string id in the interface language; ``fields`` fill its ``{name}`` places (without fields the
    text is as it is in the file)."""
    found = _entry(ident)
    shown = found if isinstance(found, str) else found[0]
    return shown.format(**fields) if fields else shown


def trn(ident: str, n: int, /, **fields: object) -> str:
    """The text of a count's id: its entry is ``[singular, plural]``, the singular for 1 (German and English share
    the rule); ``{n}`` is the count."""
    found = _entry(ident)
    if isinstance(found, list):
        found = found[0] if n == 1 else found[1]
    return found.format(n=n, **fields)


def english(ident: str, /, **fields: object) -> str:
    """The English text of an id whatever the interface language is: for a log line, a file or the command line."""
    if _catalog is None:
        _load(_active)
    found = _english.get(ident, ident)
    shown = found if isinstance(found, str) else found[0]
    return shown.format(**fields) if fields else shown


def alternatives(items: Sequence[str]) -> str:
    """``items`` as one phrase of choices (``a or b or c``), in the interface language's word and word order."""
    joined = ""
    for item in items:
        joined = tr("common.a_or_b", a=joined, b=item) if joined else item
    return joined


def N_(ident: str) -> str:
    """Mark a string id that is shown later (``tr(variable)``), for tables built at import time."""
    return ident


def places(text: str) -> set[str]:
    """The ``{name}`` places of a format string (what a translation must keep)."""
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def number(value: float, decimals: int = 0) -> str:
    """A number as the interface language writes it: 1,234.5 in English, 1.234,5 in German."""
    text = f"{value:,.{decimals}f}"
    if _active == "de":
        text = text.translate({ord(","): ".", ord("."): ","})
    return text
