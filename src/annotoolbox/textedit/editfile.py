"""The Texts app's work in a project: ``text-edits/<game>.json`` holds, per language, the texts the person wrote and the
source text each was written against, and the person's glossary terms (docs/text-editor.md, "The status of a row").

``CEditFile.load(path)`` / ``save(path)``; a missing file is an empty one, a file of another format is refused.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..infra.i18n import tr

FORMAT = 1
FOLDER = "text-edits"


def _text_or_none(value) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True)
class SEdit:
    """A text of the project: ``source`` is the source-language text it was written against (None: not a translation)."""

    text: str
    source: str | None = None


@dataclass
class CEditFile:
    m_game: str = ""
    m_source: str = "english"  # the source language last used
    m_texts: dict[str, dict[str, SEdit]] = field(default_factory=dict)  # language -> text id -> edit
    m_glossary: dict[str, dict[str, str]] = field(default_factory=dict)  # language -> source term -> translation

    def get(self, language: str, key: str) -> SEdit | None:
        return self.m_texts.get(language, {}).get(key)

    def put(self, language: str, key: str, edit: SEdit | None) -> None:
        """Keep ``edit`` for a text, or drop the text's edit when ``edit`` is None."""
        if edit is None:
            self.m_texts.get(language, {}).pop(key, None)
            if not self.m_texts.get(language, True):
                del self.m_texts[language]
        else:
            self.m_texts.setdefault(language, {})[key] = edit

    def count(self) -> int:
        return sum(len(edits) for edits in self.m_texts.values())

    def to_json(self) -> str:
        data = {
            "format": FORMAT,
            "game": self.m_game,
            "source": self.m_source,
            "texts": {
                lang: {
                    k: {"text": edit.text, **({"source": edit.source} if edit.source is not None else {})}
                    for k, edit in sorted(edits.items())
                }
                for lang, edits in sorted(self.m_texts.items())
                if edits
            },
            "glossary": {lang: dict(sorted(terms.items())) for lang, terms in sorted(self.m_glossary.items()) if terms},
        }
        return json.dumps(data, ensure_ascii=False, indent=1) + "\n"

    def save(self, path: Path) -> None:
        write_atomic(Path(path), self.to_json().encode("utf-8"))

    @classmethod
    def load(cls, path: Path, game: str = "") -> CEditFile:
        path = Path(path)
        if not path.is_file():
            return cls(game)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format") != FORMAT:
                raise ValueError(tr("texts.format_found_expected", found=data.get("format"), expected=FORMAT))
            texts = {
                str(lang): {
                    str(k): SEdit(str(value["text"]), _text_or_none(value.get("source"))) for k, value in edits.items()
                }
                for lang, edits in data.get("texts", {}).items()
            }
            glossary = {
                str(lang): {str(k): str(value) for k, value in terms.items()}
                for lang, terms in data.get("glossary", {}).items()
            }
            return cls(str(data.get("game", game)), str(data.get("source", "english")), texts, glossary)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(tr("texts.not_text_edit_file", path=path, error=exc)) from exc
