"""An archive mod or a folder of loose files of a game that replaces whole files (2070, 1404, 1701, 2205) compared with
the game's own files: the asset files asset by asset, the text files id by id, other XML by value, every other file
by its bytes.

``members_of(path, game)`` reads an archive or a folder (``SMember``: path, size, a reader), ``compare_members(game,
members, label)`` returns the ``SModEffect`` of it (the same record a mod's step is), so the views and the export treat
both alike. ``docs/mod-diff.md`` "The five questions".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..assetedit.archiveimport import index_for
from ..assetedit.assetgame import CGame
from ..formats.rda import CRdaArchive, CRdaError
from ..infra.i18n import tr
from ..modloader.patchdoc import CPatchDoc
from ..sources import loca2070
from ..sources.overlay import normalize
from .base import CGameBase
from .effect import CRun, SModEffect, data_of, text_targets
from .elementdata import data_changes
from .filechanges import classify, digest
from .model import CHANGED, CTarget


@dataclass(frozen=True)
class SMember:
    """One file of an archive or a folder: its path as the game names it (lower case), size in bytes, and how to read it."""

    path: str
    size_bytes: int
    read: Callable[[], bytes]
    source: str = ""  # the file on disk (a folder's), empty for an archive's member


def members_of(path: Path, game: CGame | None = None) -> dict[str, SMember]:
    """The members of an archive file or of a folder, by path. ``game`` gives a 2205 patch its names (its index)."""
    path = Path(path)
    if path.is_dir():
        return {
            member_key: SMember(member_key, file_path.stat().st_size, file_path.read_bytes, str(file_path))
            for file_path in sorted(path.rglob("*"))
            if file_path.is_file() and (member_key := normalize(file_path.relative_to(path)))
        }
    try:
        rda = CRdaArchive(path)
        index = index_for(game, path) if game is not None else None
        named = index.paths_in(path.name) if index is not None else {}
        out = {}
        for stored, entry in rda.entries.items():
            key = normalize(named.get(entry.offset, stored))
            out[key] = SMember(key, entry.size, lambda member_entry=entry: rda.read_entry(member_entry))
        return out
    except (CRdaError, OSError, ValueError) as exc:
        raise ValueError(tr("assets.not_archive_reads", path=path, error=exc)) from exc


def compare_members(
    game: CGame,
    members: dict[str, SMember],
    label: str,
    progress: Callable[[str], None] | None = None,
    compare_bytes: int = 2000,
) -> SModEffect:
    """``members`` against the game's files, as one step: targets for the asset, text and XML files, ``files`` for the
    rest. ``compare_bytes``: how many other files are compared byte for byte."""
    say = progress or (lambda _message: None)
    base = CGameBase(game)
    run = CRun(base, say, remember=False)
    step = SModEffect(label, label)
    compared = 0
    for path in sorted(members):
        member = members[path]
        say(path)
        data = member.read()
        found = _targets(base, run, path, data)
        if found is not None:
            for target in found:
                target.m_mods = [label]
                step.targets[target.ident] = target
            continue
        change = classify(
            base, path, member.size_bytes, lambda content=data: content, compared < compare_bytes, member.source
        )
        change.digest = change.digest or digest(data)  # the member is read anyway: two archives are told apart by it
        compared += 1
        change.mods = [label]
        step.files.append(change)
    return step


def _targets(base: CGameBase, run: CRun, path: str, data: bytes) -> list[CTarget] | None:
    """The targets of a member against the game's file of its path; None where it is a file to compare by bytes (no
    such file in the game, no XML, no text)."""
    if not base.exists(path):
        return None
    lines = base.m_layout.text_format == "lines" and base.language_of()(path) is not None
    if not lines and not path.endswith(".xml"):
        return None
    theirs = base.read_base(path)
    if theirs is None or theirs == data:
        return []
    if lines:
        return text_targets(path, _line_texts(theirs), _line_texts(data))
    try:
        doc = CPatchDoc.load(data)
        if base.language_of()(path) is not None:
            return text_targets(path, base.texts_of(CPatchDoc.load(theirs)), base.texts_of(doc))
        if doc.m_by_guid or path in base.m_files:
            run.m_touched[path] = set(doc.m_by_guid)
            run.m_session.m_docs[path] = doc
            return run.asset_targets(path, doc, base.vanilla_asset_counts(path))
        changes = data_changes(data_of(CPatchDoc.load(theirs)), data_of(doc))
    except ET.ParseError:  # the member or the game's file is no XML: compared by its bytes
        return None
    return [CTarget(path, "", CHANGED, m_changes=changes)] if changes else []


def _line_texts(raw: bytes) -> dict[str, str]:
    """An ``id=text`` file (2070, 1404) as id -> text."""
    return {str(i): text for i, text in loca2070.parse_guid_text(loca2070.decode_text_file(raw)).items()}
