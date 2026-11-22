"""One writer for a project's texts: the Texts app's edits and the texts the asset editor's change set holds, combined
(docs/text-editor.md, "One writer for both apps"). No Qt.

``combine(edits, changes_texts)`` returns the edits to write and the conflicts, one sentence each for a text id both apps
changed to different words (the Texts app's text is written). ``project_edits(project)`` reads both files of a project.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ..infra.i18n import tr
from .editfile import CEditFile, SEdit


@dataclass(frozen=True)
class SConflict:
    """A text both apps changed to different words: ``kept`` is the Texts app's, ``dropped`` the asset editor's."""

    language: str
    key: str
    kept: str
    dropped: str

    def sentence(self) -> str:
        return tr(
            "texts.text_texts_app_asset_editor",
            language=self.language,
            key=self.key,
        )


def combine(edits: CEditFile, changes_texts: dict[str, dict[str, str]]) -> tuple[CEditFile, list[SConflict]]:
    """``edits`` plus every text of ``changes_texts`` (language -> id -> text) it has no edit for."""
    out = replace(edits, m_texts={lang: dict(found) for lang, found in edits.m_texts.items()})
    conflicts: list[SConflict] = []
    for language, texts in sorted(changes_texts.items()):
        for key, text in sorted(texts.items()):
            have = out.get(language, key)
            if have is None:
                out.put(language, key, SEdit(text))
            elif have.text != text:
                conflicts.append(SConflict(language, key, have.text, text))
    return out, conflicts


def project_edits(project, edits_path: Path, change_set_path: Path) -> tuple[CEditFile, list[SConflict]]:
    """The two files of a project (a missing one is empty) as one set of edits."""
    from ..assetedit.changeset import CChangeSet

    edits = CEditFile.load(edits_path, project.game)
    texts = CChangeSet.load(change_set_path).m_texts if change_set_path.is_file() else {}
    return combine(edits, texts)
