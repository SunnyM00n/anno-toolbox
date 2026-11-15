"""The open game of the Texts app: two languages side by side, the project's edits with their undo, the status of
every row and the filters over them (docs/text-editor.md, "The user's guide"). No Qt: the window and the tests use it.

``TextDoc(files, layout, edits)``; ``load(language)`` reads a language (off the window's thread: it is the slow step),
``set_languages`` picks the pair, ``rows(Filter(...))`` the ids to show, ``status``, ``problems``, ``edit``, ``revert``.
"""

from __future__ import annotations

import functools
import re
import threading
from dataclasses import dataclass, field

from ..assetedit.gamefiles import GameFiles, Layout
from ..infra.i18n import N_, tr
from . import checks, texttable
from .editfile import Edit, EditFile
from .fontcover import FontCheck
from .glossary import Glossary, game_terms
from .stylelinks import StyleLinks
from .voicedata import Voice
from .voices import Voices

REVIEW = "\x00needs review"  # the source an imported "needs review" translation claims to have been written against

UNTRANSLATED, TRANSLATED, CHANGED, OUTDATED, NEW = "untranslated", "translated", "changed", "outdated", "new"
STYLE_ANY = "*"  # the style picker's entry for every text style
STATUSES = (UNTRANSLATED, OUTDATED, CHANGED, NEW, TRANSLATED)
STATUS_NAMES = {
    UNTRANSLATED: N_("untranslated"),
    TRANSLATED: N_("translated"),
    CHANGED: N_("changed"),
    OUTDATED: N_("outdated"),
    NEW: N_("new"),
}


def _locked(method):
    """The edits and the row lists never run together (a filter runs off the window's thread)."""

    @functools.wraps(method)
    def run(self, *args, **kwargs):
        with self.busy:
            return method(self, *args, **kwargs)

    return run


_NOT_WORDS = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]|<[^<>]*>")


@dataclass(frozen=True)
class Filter:
    """What the rows shown must match (everything set must hold)."""

    statuses: frozenset[str] = frozenset()  # none: every status
    file: str = ""  # a file's name (games with several text files)
    group: str = ""
    query: str = ""  # words: each in the id, the source, the target or the editor name
    only_problems: bool = False  # rows whose target has an error
    voiced: bool = False  # rows the game speaks


@dataclass
class _Step:
    """One undo step: per text, what the edit was before and is now."""

    label: str
    changes: list[tuple[str, str, Edit | None, Edit | None]]


@dataclass
class TextDoc:
    files: GameFiles
    layout: Layout
    edits: EditFile
    source: str = "english"
    target: str = ""
    dirty: bool = False
    loaded: dict[str, texttable.Loaded] = field(default_factory=dict)
    undo_stack: list[_Step] = field(default_factory=list)
    redo_stack: list[_Step] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.languages = texttable.languages(self.files, self.layout)
        self.names: dict[str, texttable.Meta] | None = None
        self._lock = threading.Lock()
        self.busy = threading.RLock()
        self._glossary: dict[tuple[str, str], Glossary] = {}
        self.fonts = FontCheck(self.files, self.layout.key)
        self.links = StyleLinks(self.files, self.fonts.chain) if self.fonts.chain is not None else None
        self.voices = Voices(self.files, self.layout)
        self.glyphs_ready: set[str] = set()
        if self.source not in self.languages and self.languages:
            self.source = "english" if "english" in self.languages else next(iter(self.languages))
        if not self.target:
            self.target = next((n for n in self.languages if n != self.source), self.source)

    # languages
    def load(self, language: str) -> texttable.Loaded:
        """A language read once (the slow step: run it on a background thread)."""
        with self._lock:
            if language not in self.loaded:
                if self.names is None:
                    self.names = texttable.read_names(self.files, self.layout)
                self.loaded[language] = texttable.read_language(self.files, self.layout, language, self.names)
            return self.loaded[language]

    def set_languages(self, source: str, target: str) -> None:
        self.source, self.target, self.edits.source = source, target, source

    def prepare(self, source: str, target: str) -> None:
        """Everything a pair of languages needs that reads files: both languages, the glossary, the fonts of the target
        and the voices (the slow step: run it off the window's thread, then the pair shows without a wait)."""
        for language in dict.fromkeys((source, target)):
            self.load(language)
        self.set_languages(source, target)
        self.glossary()
        self.fonts.load(target)
        self.glyphs_ready.add(target)
        if self.voices.available:
            for language in dict.fromkeys((source, target)):
                self.voices.load(language)

    def table(self, language: str) -> texttable.Table:
        return self.load(language).table

    @property
    def one_line(self) -> bool:
        return self.layout.text_format == "lines"

    # texts
    def game_text(self, language: str, key: str) -> str | None:
        entry = self.table(language).get(key)
        return entry.text if entry is not None else None

    def text(self, language: str, key: str) -> str | None:
        """The text now: the project's edit, else the game's; None when neither has one."""
        edit = self.edits.get(language, key)
        return edit.text if edit is not None else self.game_text(language, key)

    def source_text(self, key: str) -> str:
        return self.text(self.source, key) or ""

    def target_text(self, key: str) -> str:
        return self.text(self.target, key) or ""

    def entry(self, key: str) -> texttable.Entry | None:
        """The row's entry as the source language has it, else the target's."""
        return self.table(self.source).get(key) or self.table(self.target).get(key)

    def has(self, key: str) -> bool:
        """Whether a text of this id exists: in the game, in either language, or in the project."""
        return (
            key in self.table(self.source)
            or key in self.table(self.target)
            or any(key in edits for edits in self.edits.texts.values())
        )

    def ids(self) -> list[str]:
        """Every id the rows list: the source's, the target's that the source lacks, and the project's new ones."""
        seen = dict.fromkeys(self.table(self.source).by_id)
        for language in (self.target, self.source):
            seen.update(dict.fromkeys(self.table(language).by_id))
            seen.update(dict.fromkeys(self.edits.texts.get(language, {})))
        twins = self.table(self.source).twins or self.table(self.target).twins
        return _twins_after_masters(list(seen), twins) if twins else list(seen)

    # variations: the other portrait's and the gamepad's twin of a line (117)
    def twins_of(self, key: str) -> list[str]:
        """The lines that are this line's variations, or, for a variation, its main line and its other variations."""
        entry = self.entry(key)
        table = self.table(self.source) if key in self.table(self.source) else self.table(self.target)
        if entry is not None and entry.master:
            return [entry.master] + [t for t in table.twins.get(entry.master, ()) if t != key]
        return list(table.twins.get(key, ()))

    def kind_label(self, key: str) -> str:
        """What a variation is for: the player character whose portrait it is, else the kind's number."""
        entry = self.entry(key)
        if entry is None or not entry.kind:
            return ""
        return self.voices.characters.get(entry.kind) or tr("variation {id}", id=entry.kind[-6:])

    def fill_twins(self, key: str) -> int:
        """The row's target text for the twins that have the very same source text (one undo step): the number written."""
        text, source = self.target_text(key), self.source_text(key)
        items = [
            (self.target, twin, text, None)
            for twin in self.twins_of(key)
            if self.source_text(twin) == source and self.target_text(twin) != text
        ]
        return self.edit_many(items, tr("fill the twins of {id}", id=key)) if text.strip() else 0

    # voices
    def voice(self, key: str, language: str | None = None) -> Voice | None:
        return self.voices.voice(key, language or self.target)

    def playable(self, key: str) -> tuple[str, ...]:
        """Which of the two sides ("source", "target") have a voice clip for the text in their language."""
        sides = (("source", self.source), ("target", self.target))
        return tuple(side for side, language in sides if self.voices.can_play(key, language))

    def lips(self, key: str) -> str:
        """The lip file of a text's voice in the target language as a line (reads files: off the window's thread)."""
        return self.voices.lip_line(key, self.target)

    def clip(self, key: str, side: str) -> tuple[bytes, str]:
        """The voice of a text as a file a player plays (read the bank: run it off the window's thread)."""
        return self.voices.clip(key, self.source if side == "source" else self.target)

    def status(self, key: str) -> str:
        source, target = self.source, self.target
        current = self.source_text(key)
        t_edit = self.edits.get(target, key)
        in_game = key in self.table(source) or key in self.table(target)
        if t_edit is not None:
            if t_edit.source is not None and t_edit.source != current:
                return OUTDATED
            return CHANGED if in_game else NEW
        game = self.game_text(target, key)
        s_edit = self.edits.get(source, key)
        if s_edit is not None and source != target and game is not None and s_edit.text != self.game_text(source, key):
            return OUTDATED  # the source was edited and the translation was not
        if not in_game:
            return NEW
        if game is None or not game.strip():
            return UNTRANSLATED
        if source != target and game == current and any(c.isalpha() for c in _NOT_WORDS.sub("", current)):
            return UNTRANSLATED
        return TRANSLATED

    def problems(self, key: str) -> list[checks.Problem]:
        target = self.target_text(key)
        found = self.glyph_problems(target, key=key)
        if self.source == self.target or not (target or self.source_text(key)):
            return found
        return checks.check(self.source_text(key), target, self.one_line) + found

    def glyph_problems(self, text: str, style: str = "", key: str = "") -> list[checks.Problem]:
        """The characters of ``text`` the game's fonts do not draw (nothing until ``prepare`` read the target's fonts).
        ``style``: a localized font's id (``style_choices``), ``"*"`` for every style, or empty for the styles of the
        labels that show the text ``key`` where a UI file names it, else every style."""
        if self.target not in self.glyphs_ready:
            return []
        wanted = () if style == STYLE_ANY else (style,) if style else self.linked_fonts(key)
        return self.fonts.unsupported(self.target, text, wanted)

    def prepare_styles(self) -> None:
        """Read which text style each label of the game's UI files draws a text with (the first time about 40 seconds
        for 117 and 1800: run it off the window's thread; the result is kept in the cache folder)."""
        if self.links is not None:
            self.links.load()

    def linked_fonts(self, key: str) -> tuple[str, ...]:
        """The localized fonts of the text styles of the labels that show the text (empty: no UI file names it)."""
        return self.links.fonts_of(key) if self.links is not None and key else ()

    def style_choices(self) -> list[tuple[str, str]]:
        """(localized font id, its name) for the style picker, the fonts the target language is checked against."""
        if self.target not in self.glyphs_ready:
            return []
        return [(s.key, s.name) for s in self.fonts.plan(self.target).styles if s.key]

    def linked_names(self, key: str) -> list[str]:
        """The names of ``linked_fonts`` in the picker's words."""
        names = dict(self.style_choices())
        return [names.get(font, font) for font in self.linked_fonts(key)]

    def hints(self, key: str) -> list[str]:
        return self.glossary().hints(self.source_text(key), self.target_text(key))

    def glossary(self) -> Glossary:
        """The glossary of the pair of languages: the game's terms (read once) and the person's."""
        pair = (self.source, self.target)
        if pair not in self._glossary:
            terms = game_terms(self.table(self.source), self.table(self.target)) if self.source != self.target else {}
            self._glossary[pair] = Glossary(terms)
        found = self._glossary[pair]
        found.own = self.edits.glossary.get(self.target, {})
        return found

    # edits
    @_locked
    def edit(self, key: str, text: str, language: str | None = None) -> str:
        """Set a text of the project (the target's by default): the row's new status. A text equal to the game's
        takes the edit away."""
        step = self._set(language or self.target, key, text)
        if step[2] != step[3]:  # an apply that changes nothing is no step to undo and no change to save
            self._record(tr("edit {id}", id=key), [step])
        return self.status(key)

    @_locked
    def revert(self, key: str, language: str | None = None) -> str:
        language = language or self.target
        if self.edits.get(language, key) is not None:
            self._record(tr("revert {id}", id=key), [(language, key, self.edits.get(language, key), None)])
            self.edits.put(language, key, None)
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
        before = self.edits.get(language, key)
        game = self.game_text(language, key)
        # a translation equal to the game's is no edit, unless the source was edited: it then says "still right"
        confirms = language != self.source and self.edits.get(self.source, key) is not None
        if game is not None and text == game and not confirms and (source is None or source == self.source_text(key)):
            after = None
        else:
            snapshot = None if language == self.source else (self.source_text(key) if source is None else source)
            after = Edit(text, snapshot)
        self.edits.put(language, key, after)
        return (language, key, before, after)

    def _record(self, label: str, changes: list[tuple[str, str, Edit | None, Edit | None]]) -> None:
        self.undo_stack.append(_Step(label, changes))
        self.redo_stack.clear()
        self.dirty = True

    @_locked
    def undo(self) -> str:
        if not self.undo_stack:
            return ""
        step = self.undo_stack.pop()
        for language, key, before, _after in reversed(step.changes):
            self.edits.put(language, key, before)
        self.redo_stack.append(step)
        self.dirty = True
        return step.label

    @_locked
    def redo(self) -> str:
        if not self.redo_stack:
            return ""
        step = self.redo_stack.pop()
        for language, key, _before, after in step.changes:
            self.edits.put(language, key, after)
        self.undo_stack.append(step)
        self.dirty = True
        return step.label

    @_locked
    def free_id(self, start: int) -> str:
        """The smallest id from ``start`` that no language of the game and no edit uses (a new text's id)."""
        used = {k for t in self.loaded.values() for k in t.table.by_id} | {
            k for e in self.edits.texts.values() for k in e
        }
        number = start
        while str(number) in used:
            number += 1
        return str(number)

    # the rows
    def files_listed(self) -> list[str]:
        return sorted({e.file for e in self.table(self.source).entries})

    def groups_listed(self) -> list[str]:
        return sorted({e.group for e in self.table(self.source).entries if e.group})

    @_locked
    def rows(self, flt: Filter) -> list[str]:
        """The ids that match ``flt``, in the order of the source's file."""
        words = flt.query.lower().split()
        source = self.table(self.source)
        out = []
        for key in self.ids():
            entry = source.get(key) or self.table(self.target).get(key)
            if flt.file and (entry is None or entry.file != flt.file):
                continue
            if flt.group and (entry is None or entry.group != flt.group):
                continue
            if flt.statuses and self.status(key) not in flt.statuses:
                continue
            voice = self.voice(key) if flt.voiced or words else None
            if flt.voiced and voice is None:
                continue
            if words and not _matches(
                words, key, self.source_text(key), self.target_text(key), entry.name if entry else "", voice
            ):
                continue
            if flt.only_problems and not any(p.severity == checks.ERROR for p in self.problems(key)):
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


def _matches(words: list[str], key: str, source: str, target: str, name: str, voice: Voice | None = None) -> bool:
    speaker = voice.speaker if voice is not None else ""
    haystack = f"{key}\n{source}\n{target}\n{name}\n{speaker}".lower()
    return all(w in haystack for w in words)


def _twins_after_masters(ids: list[str], twins: dict[str, list[str]]) -> list[str]:
    """``ids`` with each variation moved to stand right behind its main line (when both are listed)."""
    present = set(ids)
    owner = {twin: master for master, found in twins.items() for twin in found if master in present}
    out: list[str] = []
    for key in ids:
        if key not in owner:
            out.append(key)
            out += [t for t in twins.get(key, ()) if t in present and owner.get(t) == key]
    return out
