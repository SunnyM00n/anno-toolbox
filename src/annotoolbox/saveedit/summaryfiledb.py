"""The summary of an Anno 1800 or 2205 save: an outline of each FileDB document and, where the save has 117's header
tags, the load list's rows read as 117's are (``docs/save-editor.md``, "The other games"). Built from the documented
FileDB format and 117's save layout with synthetic samples; no save of either game was available, so the tag names
of 1800 are assumed to be 117's (its descendant) and 2205's are not assumed at all.
"""

from __future__ import annotations

from ..formats import filedb
from ..infra.i18n import N_
from . import summary117
from .container import FILEDB, SSaveFile
from .records import CSummary, SSection, STable

OUTLINE_LIMIT = 40  # top-level tags listed per document


def outline(save: SSaveFile) -> SSection:
    """Per FileDB document: its version and the tags at its top (name, how many nodes and values it holds)."""
    table = STable([N_("saves.archive_part"), N_("saves.filedb_version"), N_("saves.tag"), N_("saves.holds")])
    for part in save.parts:
        if part.m_kind != FILEDB:
            continue
        version = str(filedb.version(part.m_doc))
        tags = list(part.tree().children) if part.tree() is not None else []
        for child in tags[:OUTLINE_LIMIT]:
            holds = f"{len(child.children):,}" if isinstance(child, filedb.STag) else f"{len(child.data):,} bytes"
            table.rows.append([part.m_name, version, child.name, holds])
        if len(tags) > OUTLINE_LIMIT:
            table.rows.append([part.m_name, version, f"... {len(tags) - OUTLINE_LIMIT} more", ""])
    return SSection(N_("saves.documents"), table=table)


def _load_list(summary: CSummary, save: SSaveFile) -> bool:
    """117's version, mods and setup rows when the header holds a ``CorporationProfile``."""
    meta, header = save.part("meta.a7s"), save.part("header.a7s")
    top = header.tree().find("CorporationProfile") if header is not None and header.m_kind == FILEDB else None
    if top is None:
        return False
    summary117.version_rows(summary, meta.tree() if meta is not None and meta.m_kind == FILEDB else None, top)
    summary117.mods_rows(summary, top)
    summary117.setup_rows(summary, top)
    return True


def summarize(save: SSaveFile) -> CSummary:
    summary = CSummary(save.game)
    if save.game == "anno1800" and _load_list(summary, save):
        summary.note(N_("saves.read_anno_117s_tag_names"))
    else:
        summary.note(N_("saves.no_sample_games_saves_pinned_sentence"))
    summary.m_sections.append(outline(save))
    return summary
