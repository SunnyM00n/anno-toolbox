"""The Toolbox's interface language: ``tr`` shows the text of a string id, ``trn`` a count's, ``N_`` marks an id that
is shown later, ``number`` formats a number. Pure Python, no Qt, so the apps and the command line share it.

``use(code)`` selects the language (the suite settings' ``language``; ``ANNO_TOOLBOX_LANG`` overrides it). A language
is one file, ``infra/lang/<code>.json``: sections by app, each a sorted list of id -> text; an id a language lacks is
shown in English. A user's override folder (``override_dir``) holds files of the same shape that win over the shipped
ones, entry by entry; ``reload`` re-reads them. The design is in ``docs/tool-suite.md`` section 25; ``scripts/check_i18n.py`` compares the files
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
OVERRIDE_ENV = "ANNO_TOOLBOX_LANG_DIR"  # the override folder, when it is not ``lang`` in the config folder
META = "meta"  # the section of a language file that holds its own name ({"name": "Deutsch"}), not strings
ID = re.compile(r"[a-z][a-z0-9]*(\.[a-z0-9_]+)+")  # <section>.<name>, or <section>.<group>.<name>

Entry = str | list[str]
_g_active = DEFAULT
_g_catalog: dict[str, Entry] | None = None  # the active language's strings, read at the first lookup
_g_english: dict[str, Entry] = {}  # the default language's, shown for an id the active one lacks
_g_missing: set[str] = set()


def is_id(text: str) -> bool:
    """Whether a text has the shape of a string id (``saves.emperor``, ``islands.menu.file``)."""
    return ID.fullmatch(text) is not None


def section_of(ident: str) -> str:
    """The section of a language file an id is filed in: its first part."""
    return ident.split(".", 1)[0]


def override_dir() -> Path:
    """The folder of the user's own language files (the translation window's save in an installed program): they
    are read on top of the shipped ones."""
    given = os.environ.get(OVERRIDE_ENV)
    if given:
        return Path(given)
    from .. import paths

    return paths.config_dir() / "lang"


def read_file(code: str) -> dict[str, dict]:
    """The sections of a language's shipped file (``META`` and one per app); an empty dict when it has none."""
    return read_path(LANG_DIR / f"{code}.json")


def read_path(path: Path) -> dict[str, dict]:
    """The sections of a language file at ``path``; an empty dict when there is none."""
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def read_override(code: str) -> dict[str, dict]:
    """The sections of a language's override file; an empty dict when it has none or it does not read."""
    try:
        sections = read_path(override_dir() / f"{code}.json")
    except (ValueError, OSError):
        return {}
    return {k: value for k, value in sections.items() if isinstance(value, dict)} if isinstance(sections, dict) else {}


def _is_plural(value: object) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(isinstance(x, str) for x in value)


def flatten(sections: dict[str, dict]) -> dict[str, Entry]:
    """The entries of a file's sections as one id -> text dict (``META`` left out)."""
    merged: dict[str, Entry] = {}
    for section, entries in sections.items():
        if section != META and isinstance(entries, dict):
            merged.update({k: value for k, value in entries.items() if isinstance(value, str) or _is_plural(value)})
    return merged


def load_catalog(code: str) -> dict[str, Entry]:
    """A language's strings, id -> text (a count's is ``[singular, plural]``), the shipped file's with the user's
    override file's entries on top."""
    return {**flatten(read_file(code)), **flatten(read_override(code))}


def shipped_languages() -> dict[str, str]:
    """The languages with a shipped file, code -> its own name (a file's ``meta``); the default first, the rest by code."""
    found = {DEFAULT: read_file(DEFAULT).get(META, {}).get("name", "English")}
    for path in sorted(LANG_DIR.glob("*.json")):
        if path.stem != DEFAULT:
            found[path.stem] = read_file(path.stem).get(META, {}).get("name", path.stem)
    return found


def languages() -> dict[str, str]:
    """The languages to choose from, code -> its own name: the shipped ones and those of the user's override folder,
    each once it has entries (a language file just begun is not offered); the default first, the rest by code."""
    names = dict(shipped_languages())
    folder = override_dir()
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        names.setdefault(path.stem, read_override(path.stem).get(META, {}).get("name", path.stem))
    for code in list(names):
        if code != DEFAULT and not load_catalog(code):
            del names[code]
    names = {code: read_override(code).get(META, {}).get("name") or name for code, name in names.items()}
    return {
        DEFAULT: names[DEFAULT],
        **dict(
            sorted(
                (language_code, language_name)
                for language_code, language_name in names.items()
                if language_code != DEFAULT
            )
        ),
    }


def dump_file(sections: dict[str, dict]) -> str:
    """A language file as text: ``meta`` first, then the sections by name, one sorted entry per line (UTF-8, LF)."""
    names = (
        [META, *sorted(section for section in sections if section != META)] if META in sections else sorted(sections)
    )
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
    global _g_active, _g_catalog, _g_english
    _g_english = load_catalog(DEFAULT)
    _g_catalog = _g_english if code == DEFAULT else load_catalog(code)
    _g_active = code
    _g_missing.clear()


def reload() -> None:
    """Read the active language's files again (after the translation window saved): texts looked up from now on are
    the new ones; a label already set stays as it is until its window is made again."""
    _load(_g_active)


def text_of(ident: str) -> Entry | None:
    """An id's entry in the active language, else English's, else None (no mark is made of a lookup)."""
    if _g_catalog is None:
        _load(_g_active)
    found = _g_catalog.get(ident)
    return found if found is not None else _g_english.get(ident)


def use(code: str | None = None) -> str:
    """Select the interface language (``None``: the environment's, else the default); returns the code in effect."""
    code = (os.environ.get(ENV_VAR) or code or DEFAULT).strip().lower()
    if code not in languages():
        code = DEFAULT
    _load(code)
    return code


def current() -> str:
    return _g_active


def missing() -> frozenset[str]:
    """What was looked up without an entry in the active language (shown in English, or as it is when English has
    none either); for tests and checks."""
    return frozenset(_g_missing)


def _entry(ident: str) -> Entry:
    """An id's entry in the active language, else English's, else the id itself (a text that is no id passes)."""
    if _g_catalog is None:
        _load(_g_active)
    found = _g_catalog.get(ident)
    if found is None:
        _g_missing.add(ident)
        found = _g_english.get(ident, ident)
    return found


def tr(ident: str, /, **fields: object) -> str:
    """The text of a string id in the interface language; ``fields`` fill its ``{name}`` places (without fields the
    text is as it is in the file)."""
    found = _entry(ident)
    shown = found if isinstance(found, str) else found[0]
    try:
        return shown.format(**fields) if fields else shown
    except (KeyError, IndexError, ValueError):  # an override's text with other places than the code passes
        return english(ident, **fields)


def trn(ident: str, count: int, /, **fields: object) -> str:
    """The text of a count's id: its entry is ``[singular, plural]``, the singular for 1 (German and English share
    the rule); ``{n}`` is the count."""
    found = _entry(ident)
    if isinstance(found, list):
        found = found[0] if count == 1 else found[1]
    try:
        return found.format(n=count, **fields)
    except (KeyError, IndexError, ValueError):
        english_form = _g_english.get(ident, ident)
        if isinstance(english_form, list):
            english_form = english_form[0] if count == 1 else english_form[1]
        return english_form.format(n=count, **fields)


def english(ident: str, /, **fields: object) -> str:
    """The English text of an id whatever the interface language is: for a log line, a file or the command line."""
    if _g_catalog is None:
        _load(_g_active)
    found = _g_english.get(ident, ident)
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
    if _g_active == "de":
        text = text.translate({ord(","): ".", ord("."): ","})
    return text
