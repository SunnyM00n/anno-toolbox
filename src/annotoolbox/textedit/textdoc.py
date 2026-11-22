"""The open game of the Texts app: two languages side by side, the project's edits with their undo, the status of
every row and the filters over them (docs/text-editor.md, "The user's guide"). No Qt: the window and the tests use it.

``CTextDoc(files, layout, edits)``; ``load(language)`` reads a language (off the window's thread: it is the slow step),
``set_languages`` picks the pair, ``rows(Filter(...))`` the ids to show, ``status``, ``problems``, ``edit``, ``revert``.
"""

from __future__ import annotations

import functools
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..assetedit.gamefiles import CGameFiles, SLayout
from ..infra.i18n import N_, tr
from . import checks, texttable
from .editfile import CEditFile, SEdit
from .fontcover import CFontCheck
from .glossary import SGlossary, game_terms
from .stylelinks import CStyleLinks
from .textfont import CFaces
from .voicedata import SVoice
from .voices import CVoices

REVIEW = "\x00needs review"  # the source an imported "needs review" translation claims to have been written against

UNTRANSLATED, TRANSLATED, CHANGED, OUTDATED, NEW = "untranslated", "translated", "changed", "outdated", "new"
STYLE_ANY = "*"  # the style picker's entry for every text style
STATUSES = (UNTRANSLATED, OUTDATED, CHANGED, NEW, TRANSLATED)
STATUS_NAMES = {
    UNTRANSLATED: N_("texts.untranslated"),
    TRANSLATED: N_("texts.translated"),
    CHANGED: N_("common.changed"),
    OUTDATED: N_("texts.outdated"),
    NEW: N_("assets.new"),
}


def _locked(method):
    """The edits and the row lists never run together (a filter runs off the window's thread)."""

    @functools.wraps(method)
    def run(self, *args, **kwargs):
        with self.m_busy:
            return method(self, *args, **kwargs)

    return run


_NOT_WORDS = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]|<[^<>]*>")


@dataclass(frozen=True)
class SFilter:
    """What the rows shown must match (everything set must hold)."""

    statuses: frozenset[str] = frozenset()  # none: every status
    file: str = ""  # a file's name (games with several text files)
    group: str = ""
    query: str = ""  # words: each in the id, the source, the target or the editor name
    only_problems: bool = False  # rows whose target has an error
    voiced: bool = False  # rows the game speaks


@dataclass
class _SStep:
    """One undo step: per text, what the edit was before and is now."""

    label: str
    changes: list[tuple[str, str, SEdit | None, SEdit | None]]


@dataclass
class CTextDoc:
    m_files: CGameFiles
    m_layout: SLayout
    m_edits: CEditFile
    m_source: str = "english"
    m_target: str = ""
    m_dirty: bool = False
    m_loaded: dict[str, texttable.SLoaded] = field(default_factory=dict)
    m_undo_stack: list[_SStep] = field(default_factory=list)
    m_redo_stack: list[_SStep] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.m_languages = texttable.languages(self.m_files, self.m_layout)
        self.m_names: dict[str, texttable.SMeta] | None = None
        self._m_lock = threading.Lock()
        self.m_busy = threading.RLock()
        self._m_glossary: dict[tuple[str, str], SGlossary] = {}
        self.m_fonts = CFontCheck(self.m_files, self.m_layout.key)
        self.m_links = CStyleLinks(self.m_files, self.m_fonts.chain) if self.m_fonts.chain is not None else None
        self.m_faces = CFaces(self.m_files, self.m_fonts, self.m_layout.key)
        self.m_voices = CVoices(self.m_files, self.m_layout)
        self.m_glyphs_ready: set[str] = set()
        if self.m_source not in self.m_languages and self.m_languages:
            self.m_source = "english" if "english" in self.m_languages else next(iter(self.m_languages))
        if not self.m_target:
            self.m_target = next(
                (language for language in self.m_languages if language != self.m_source), self.m_source
            )

    # languages
    def load(self, language: str) -> texttable.SLoaded:
        """A language read once (the slow step: run it on a background thread)."""
        with self._m_lock:
            if language not in self.m_loaded:
                if self.m_names is None:
                    self.m_names = texttable.read_names(self.m_files, self.m_layout)
                self.m_loaded[language] = texttable.read_language(self.m_files, self.m_layout, language, self.m_names)
            return self.m_loaded[language]

    def set_languages(self, source: str, target: str) -> None:
        self.m_source, self.m_target, self.m_edits.m_source = source, target, source

    def prepare(self, source: str, target: str) -> None:
        """Everything a pair of languages needs that reads files: both languages, the glossary, the fonts of the target
        and the voices (the slow step: run it off the window's thread, then the pair shows without a wait)."""
        for language in dict.fromkeys((source, target)):
            self.load(language)
        self.set_languages(source, target)
        self.glossary()
        self.m_fonts.load(target)
        self.m_glyphs_ready.add(target)
        if self.m_voices.available:
            for language in dict.fromkeys((source, target)):
                self.m_voices.load(language)

    def table(self, language: str) -> texttable.STable:
        return self.load(language).table

    @property
    def one_line(self) -> bool:
        return self.m_layout.text_format == "lines"

    # texts
    def game_text(self, language: str, key: str) -> str | None:
        entry = self.table(language).get(key)
        return entry.text if entry is not None else None

    def text(self, language: str, key: str) -> str | None:
        """The text now: the project's edit, else the game's; None when neither has one."""
        edit = self.m_edits.get(language, key)
        return edit.text if edit is not None else self.game_text(language, key)

    def source_text(self, key: str) -> str:
        return self.text(self.m_source, key) or ""

    def target_text(self, key: str) -> str:
        return self.text(self.m_target, key) or ""

    def entry(self, key: str) -> texttable.SEntry | None:
        """The row's entry as the source language has it, else the target's."""
        return self.table(self.m_source).get(key) or self.table(self.m_target).get(key)

    def has(self, key: str) -> bool:
        """Whether a text of this id exists: in the game, in either language, or in the project."""
        return (
            key in self.table(self.m_source)
            or key in self.table(self.m_target)
            or any(key in edits for edits in self.m_edits.m_texts.values())
        )

    def ids(self) -> list[str]:
        """Every id the rows list: the source's, the target's that the source lacks, and the project's new ones."""
        seen = dict.fromkeys(self.table(self.m_source).by_id)
        for language in (self.m_target, self.m_source):
            seen.update(dict.fromkeys(self.table(language).by_id))
            seen.update(dict.fromkeys(self.m_edits.m_texts.get(language, {})))
        twins = self.table(self.m_source).twins or self.table(self.m_target).twins
        return _twins_after_masters(list(seen), twins) if twins else list(seen)

    # variations: the other portrait's and the gamepad's twin of a line (117)
    def twins_of(self, key: str) -> list[str]:
        """The lines that are this line's variations, or, for a variation, its main line and its other variations."""
        entry = self.entry(key)
        table = self.table(self.m_source) if key in self.table(self.m_source) else self.table(self.m_target)
        if entry is not None and entry.master:
            return [entry.master] + [twin for twin in table.twins.get(entry.master, ()) if twin != key]
        return list(table.twins.get(key, ()))

    def kind_label(self, key: str) -> str:
        """What a variation is for: the player character whose portrait it is, else the kind's number."""
        entry = self.entry(key)
        if entry is None or not entry.kind:
            return ""
        return self.m_voices.m_characters.get(entry.kind) or tr("texts.variation_id", id=entry.kind[-6:])

    def fill_twins(self, key: str) -> int:
        """The row's target text for the twins that have the very same source text (one undo step): the number written."""
        text, source = self.target_text(key), self.source_text(key)
        items = [
            (self.m_target, twin, text, None)
            for twin in self.twins_of(key)
            if self.source_text(twin) == source and self.target_text(twin) != text
        ]
        return self.edit_many(items, tr("texts.fill_twins_id", id=key)) if text.strip() else 0

    # voices
    def voice(self, key: str, language: str | None = None) -> SVoice | None:
        return self.m_voices.voice(key, language or self.m_target)

    def playable(self, key: str) -> tuple[str, ...]:
        """Which of the two sides ("source", "target") have a voice clip for the text in their language."""
        sides = (("source", self.m_source), ("target", self.m_target))
        return tuple(side for side, language in sides if self.m_voices.can_play(key, language))

    def lips(self, key: str) -> str:
        """The lip file of a text's voice in the target language as a line (reads files: off the window's thread)."""
        return self.m_voices.lip_line(key, self.m_target)

    def clip(self, key: str, side: str) -> tuple[bytes, str]:
        """The voice of a text as a file a player plays (read the bank: run it off the window's thread)."""
        return self.m_voices.clip(key, self.m_source if side == "source" else self.m_target)

    def clip_file(self, key: str, side: str) -> Path:
        """``clip`` as a file of the cache folder, written once (off the window's thread)."""
        return self.m_voices.clip_file(key, self.m_source if side == "source" else self.m_target)

    def status(self, key: str) -> str:
        source, target = self.m_source, self.m_target
        current = self.source_text(key)
        target_edit = self.m_edits.get(target, key)
        in_game = key in self.table(source) or key in self.table(target)
        if target_edit is not None:
            if target_edit.source is not None and target_edit.source != current:
                return OUTDATED
            return CHANGED if in_game else NEW
        game = self.game_text(target, key)
        source_edit = self.m_edits.get(source, key)
        if (
            source_edit is not None
            and source != target
            and game is not None
            and source_edit.text != self.game_text(source, key)
        ):
            return OUTDATED  # the source was edited and the translation was not
        if not in_game:
            return NEW
        if game is None or not game.strip():
            return UNTRANSLATED
        if (
            source != target
            and game == current
            and any(character.isalpha() for character in _NOT_WORDS.sub("", current))
        ):
            return UNTRANSLATED
        return TRANSLATED

    def problems(self, key: str) -> list[checks.SProblem]:
        target = self.target_text(key)
        found = self.glyph_problems(target, key=key)
        if self.m_source == self.m_target or not (target or self.source_text(key)):
            return found
        return checks.check(self.source_text(key), target, self.one_line) + found

    def glyph_problems(self, text: str, style: str = "", key: str = "") -> list[checks.SProblem]:
        """The characters of ``text`` the game's fonts do not draw (nothing until ``prepare`` read the target's fonts).
        ``style``: a localized font's id (``style_choices``), ``"*"`` for every style, or empty for the styles of the
        labels that show the text ``key`` where a UI file names it, else every style."""
        if self.m_target not in self.m_glyphs_ready:
            return []
        wanted = () if style == STYLE_ANY else (style,) if style else self.linked_fonts(key)
        return self.m_fonts.unsupported(self.m_target, text, wanted)

    def prepare_styles(self) -> None:
        """Read which text style each label of the game's UI files draws a text with (the first time about 40 seconds
        for 117 and 1800: run it off the window's thread; the result is kept in the cache folder)."""
        if self.m_links is not None:
            self.m_links.load()

    def linked_fonts(self, key: str) -> tuple[str, ...]:
        """The localized fonts of the text styles of the labels that show the text (empty: no UI file names it)."""
        return self.m_links.fonts_of(key) if self.m_links is not None and key else ()

    def style_choices(self) -> list[tuple[str, str]]:
        """(localized font id, its name) for the style picker, the fonts the target language is checked against."""
        if self.m_target not in self.m_glyphs_ready:
            return []
        return [
            (style_faces.key, style_faces.name)
            for style_faces in self.m_fonts.plan(self.m_target).styles
            if style_faces.key
        ]

    def face(self, style: str, key: str) -> tuple[str | None, str]:
        """(the Qt family the target language's text ``key`` is shown in under the picked style, why it is not the game's)."""
        if self.m_target not in self.m_glyphs_ready:  # the fonts of the target are read by ``prepare``
            return None, ""
        return self.m_faces.family(self.m_target, style, self.linked_fonts(key))

    def linked_names(self, key: str) -> list[str]:
        """The names of ``linked_fonts`` in the picker's words."""
        names = dict(self.style_choices())
        return [names.get(font, font) for font in self.linked_fonts(key)]

    def hints(self, key: str) -> list[str]:
        return self.glossary().hints(self.source_text(key), self.target_text(key))

    def glossary(self) -> SGlossary:
        """The glossary of the pair of languages: the game's terms (read once) and the person's."""
        pair = (self.m_source, self.m_target)
        if pair not in self._m_glossary:
            terms = (
                game_terms(self.table(self.m_source), self.table(self.m_target))
                if self.m_source != self.m_target
                else {}
            )
            self._m_glossary[pair] = SGlossary(terms)
        found = self._m_glossary[pair]
        found.own = self.m_edits.m_glossary.get(self.m_target, {})
        return found

    # edits
    @_locked
    def edit(self, key: str, text: str, language: str | None = None) -> str:
        """Set a text of the project (the target's by default): the row's new status. A text equal to the game's
        takes the edit away."""
        step = self._set(language or self.m_target, key, text)
        if step[2] != step[3]:  # an apply that changes nothing is no step to undo and no change to save
            self._record(tr("texts.edit_id", id=key), [step])
        return self.status(key)

    @_locked
    def revert(self, key: str, language: str | None = None) -> str:
        language = language or self.m_target
        if self.m_edits.get(language, key) is not None:
            self._record(tr("texts.revert_id", id=key), [(language, key, self.m_edits.get(language, key), None)])
            self.m_edits.put(language, key, None)
        return self.status(key)

    @_locked
    def edit_many(self, items: list[tuple[str, str, str, str | None]], label: str) -> int:
        """Several texts as one undo step: (language, id, text, the source it was written against or None for the
        source as it is now); the number that changed something."""
        steps = []
        for language, key, text, source in items:
            step = self._set(language, key, text, source)
            if step[2] != step[3]:
                steps.append(step)
        if steps:
            self._record(label, steps)
        return len(steps)

    def _set(self, language: str, key: str, text: str, source: str | None = None):
        text = text.replace("\r\n", "\n").replace("\r", "\n")  # the files break lines their own way
        before = self.m_edits.get(language, key)
        game = self.game_text(language, key)
        # a translation equal to the game's is no edit, unless the source was edited: it then says "still right"
        confirms = language != self.m_source and self.m_edits.get(self.m_source, key) is not None
        if game is not None and text == game and not confirms and (source is None or source == self.source_text(key)):
            after = None
        else:
            snapshot = None if language == self.m_source else (self.source_text(key) if source is None else source)
            after = SEdit(text, snapshot)
        self.m_edits.put(language, key, after)
        return (language, key, before, after)

    def _record(self, label: str, changes: list[tuple[str, str, SEdit | None, SEdit | None]]) -> None:
        self.m_undo_stack.append(_SStep(label, changes))
        self.m_redo_stack.clear()
        self.m_dirty = True

    @_locked
    def undo(self) -> str:
        if not self.m_undo_stack:
            return ""
        step = self.m_undo_stack.pop()
        for language, key, before, _after in reversed(step.changes):
            self.m_edits.put(language, key, before)
        self.m_redo_stack.append(step)
        self.m_dirty = True
        return step.label

    @_locked
    def redo(self) -> str:
        if not self.m_redo_stack:
            return ""
        step = self.m_redo_stack.pop()
        for language, key, _before, after in step.changes:
            self.m_edits.put(language, key, after)
        self.m_undo_stack.append(step)
        self.m_dirty = True
        return step.label

    @_locked
    def free_id(self, start: int) -> str:
        """The smallest id from ``start`` that no language of the game and no edit uses (a new text's id)."""
        used = {k for loaded_language in self.m_loaded.values() for k in loaded_language.table.by_id} | {
            k for edits in self.m_edits.m_texts.values() for k in edits
        }
        number = start
        while str(number) in used:
            number += 1
        return str(number)

    # the rows
    def files_listed(self) -> list[str]:
        return sorted({entry.file for entry in self.table(self.m_source).entries})

    def groups_listed(self) -> list[str]:
        return sorted({entry.group for entry in self.table(self.m_source).entries if entry.group})

    @_locked
    def rows(self, row_filter: SFilter) -> list[str]:
        """The ids that match ``flt``, in the order of the source's file."""
        words = row_filter.query.lower().split()
        source = self.table(self.m_source)
        out = []
        for key in self.ids():
            entry = source.get(key) or self.table(self.m_target).get(key)
            if row_filter.file and (entry is None or entry.file != row_filter.file):
                continue
            if row_filter.group and (entry is None or entry.group != row_filter.group):
                continue
            if row_filter.statuses and self.status(key) not in row_filter.statuses:
                continue
            voice = self.voice(key) if row_filter.voiced or words else None
            if row_filter.voiced and voice is None:
                continue
            if words and not _matches(
                words, key, self.source_text(key), self.target_text(key), entry.name if entry else "", voice
            ):
                continue
            if row_filter.only_problems and not any(problem.severity == checks.ERROR for problem in self.problems(key)):
                continue
            out.append(key)
        return out

    @_locked
    def counts(self) -> dict[str, int]:
        """How many rows have each status."""
        found = dict.fromkeys(STATUSES, 0)
        for key in self.ids():
            found[self.status(key)] += 1
        return found


def _matches(words: list[str], key: str, source: str, target: str, name: str, voice: SVoice | None = None) -> bool:
    speaker = voice.speaker if voice is not None else ""
    haystack = f"{key}\n{source}\n{target}\n{name}\n{speaker}".lower()
    return all(word in haystack for word in words)


def _twins_after_masters(ids: list[str], twins: dict[str, list[str]]) -> list[str]:
    """``ids`` with each variation moved to stand right behind its main line (when both are listed)."""
    present = set(ids)
    owner = {twin: master for master, found in twins.items() for twin in found if master in present}
    out: list[str] = []
    for key in ids:
        if key not in owner:
            out.append(key)
            out += [
                variation for variation in twins.get(key, ()) if variation in present and owner.get(variation) == key
            ]
    return out
