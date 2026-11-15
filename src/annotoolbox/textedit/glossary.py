"""The terms of a game per language: the game's own short names read from its texts, and the person's own (docs/text-editor.md,
"The glossary"). A term is a hint for a translator, never an error.

``Glossary.terms_in(text)`` finds the terms of a source text; ``game_terms(source, target)`` reads the game's pairs;
``hints`` says which terms a target text does not carry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..infra.i18n import tr
from .texttable import Table

MAX_WORDS, MAX_CHARS, MIN_CHARS = 3, 32, 4
_WORD = re.compile(r"[^\W\d_][\w'-]*")
_SENTENCE = re.compile(r"[.!?:;{}<>\[\]()\d]")


@dataclass
class Glossary:
    """Source term -> its translation, for one target language; ``own`` (the person's terms) win over ``game``."""

    game: dict[str, str] = field(default_factory=dict)
    own: dict[str, str] = field(default_factory=dict)

    def translation(self, term: str) -> str | None:
        return self.own.get(term) or self.game.get(term)

    def terms_in(self, text: str) -> list[str]:
        """The terms a source text holds, as written in it (case counts: ``Time`` is a term, ``time`` a word)."""
        words = list(_WORD.finditer(text))
        found: list[str] = []
        for i in range(len(words)):
            for n in range(MAX_WORDS, 0, -1):
                if i + n > len(words):
                    continue
                term = " ".join(w.group() for w in words[i : i + n])
                if text[words[i].start() : words[i + n - 1].end()] == term and self.translation(term):
                    found.append(term)
                    break
        return list(dict.fromkeys(found))

    def hints(self, source: str, target: str) -> list[str]:
        """A sentence per term of ``source`` whose translation ``target`` does not hold."""
        lowered = target.lower()
        out = []
        for term in self.terms_in(source):
            wanted = self.translation(term) or ""
            if wanted and wanted.lower() not in lowered:
                out.append(tr("{term} is {wanted} in the glossary", term=term, wanted=wanted))
        return out


def game_terms(source: Table, target: Table) -> dict[str, str]:
    """The game's own names as a term list: the entries of one to three words with a capital letter and no markup, source
    text -> target text of the same id; a term the game translates two ways is left out."""
    pairs: dict[str, set[str]] = {}
    for entry in source.entries:
        text, there = entry.text.strip(), target.get(entry.id)
        if (
            not (MIN_CHARS <= len(text) <= MAX_CHARS and text[:1].isupper() and not _SENTENCE.search(text))
            or there is None
        ):
            continue
        if len(text.split()) <= MAX_WORDS and there.text.strip() and "\n" not in there.text:
            pairs.setdefault(text, set()).add(there.text.strip())
    return {term: next(iter(found)) for term, found in pairs.items() if len(found) == 1 and term != next(iter(found))}
