"""What a project's texts become as files, the way each game reads them (docs/text-editor.md, "How a mod adds or
overrides a text").

``render(files, layout, edits, mod_id, log)`` returns game path -> bytes: ModOps per language file for the games with a
mod loader (117, 1800), whole text files for the others (2205, 2070, 1404, 1701). A new id is written into every language
the install holds, the source language's text where the language has none: a language file that lacks an id shows
"missing" and the game never falls back.
"""

from __future__ import annotations

from collections.abc import Callable

from ..assetedit.gamefiles import GameFiles, Layout
from ..assetedit.modops import text_ops
from ..infra.i18n import tr
from ..mod.modxml import modops
from . import textfiles, texttable
from .editfile import EditFile

COMMENT = "written by the Texts app"


def reference_language(languages: dict[str, str], edits: EditFile) -> str:
    """The language whose table says which ids are the game's (the source language of the edits, else English)."""
    for name in (edits.source, "english"):
        if name in languages:
            return name
    return next(iter(languages), "")


def texts_per_language(files: GameFiles, layout: Layout, edits: EditFile) -> dict[str, dict[str, str]]:
    """Language -> id -> text to write: the edits, and for every id the game does not have, a text in every language the
    install holds (the edit of that language, else the source language's, else any)."""
    languages = texttable.languages(files, layout)
    reference = reference_language(languages, edits)
    known = texttable.read_language(files, layout, reference).table if reference else texttable.Table()
    new = {k for edit in edits.texts.values() for k in edit if k not in known}
    out: dict[str, dict[str, str]] = {
        lang: {k: e.text for k, e in found.items()} for lang, found in edits.texts.items()
    }
    for key in sorted(new):
        fallback = next(
            (
                e[key].text
                for lang in (edits.source, "english", *edits.texts)
                if (e := edits.texts.get(lang, {})) and key in e
            ),
            "",
        )
        for language in languages:
            out.setdefault(language, {}).setdefault(key, fallback)
    return {lang: texts for lang, texts in out.items() if texts}


def render(
    files: GameFiles, layout: Layout, edits: EditFile, mod_id: str, log: Callable[[str], None] = print
) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    languages = texttable.languages(files, layout)
    for language, texts in texts_per_language(files, layout, edits).items():
        if layout.texts and language not in languages and texttable.style_of(layout) == textfiles.XML:
            log(
                tr(
                    "{language}: the install has no such text file, {count} texts left out",
                    language=language,
                    count=len(texts),
                )
            )
            continue
        loaded = texttable.read_language(files, layout, language)
        if "modops" in layout.outputs:
            path = layout.texts.format(language=layout.language_code(language))
            out[path] = modops(text_ops(texts, layout, set(loaded.table.by_id)), COMMENT).encode("utf-8")
        else:
            out |= whole_files(loaded, layout, texts, mod_id)
        log(f"{language}: {len(texts)} texts")
    return out


def whole_files(loaded: texttable.Loaded, layout: Layout, texts: dict[str, str], mod_id: str) -> dict[str, bytes]:
    """The language's files that hold an edited text, whole with those texts replaced in place; the ids no file holds in
    the one XML file, or in ``<mod id>.txt`` beside the line files."""
    out, table = {}, loaded.table
    new = {k: v for k, v in texts.items() if k not in table}
    for path, parsed in loaded.files.items():
        mine = {k: v for k, v in texts.items() if k in table and table.by_id[k].file == path}
        xml_new = new if parsed.style == textfiles.XML else {}
        data = parsed.render(mine, xml_new)
        if data != parsed.raw:
            out[path] = data
    if new and texttable.style_of(layout) == textfiles.LINES:
        code = layout.language_code(loaded.language)
        text = "".join(f"{k}={textfiles.one_line(v)}\r\n" for k, v in new.items())
        out[f"data/loca/{code}/txt/{mod_id}.txt"] = textfiles.encode(text, "utf-16")
    return out
