"""The summary of an Anno 1800 or 2205 save: an outline of each FileDB document and, where the save has 117's header
tags, the load list's rows read as 117's are (``docs/save-editor.md``, "The other games"). Built from the documented
FileDB format and 117's save layout with synthetic samples; no save of either game was available, so the tag names
of 1800 are assumed to be 117's (its descendant) and 2205's are not assumed at all.
"""

from __future__ import annotations

from ..formats import filedb
from ..infra.i18n import N_
from . import summary117
from .container import FILEDB, SaveFile
from .records import Section, Summary, Table

OUTLINE_LIMIT = 40  # top-level tags listed per document


def outline(save: SaveFile) -> Section:
    """Per FileDB document: its version and the tags at its top (name, how many nodes and values it holds)."""
    table = Table([N_("Archive part"), N_("FileDB version"), N_("Tag"), N_("Holds")])
    for part in save.parts:
        if part.kind != FILEDB:
            continue
        version = str(filedb.version(part.doc))
        tags = list(part.tree().children) if part.tree() is not None else []
        for child in tags[:OUTLINE_LIMIT]:
            holds = f"{len(child.children):,}" if isinstance(child, filedb.Tag) else f"{len(child.data):,} bytes"
            table.rows.append([part.name, version, child.name, holds])
        if len(tags) > OUTLINE_LIMIT:
            table.rows.append([part.name, version, f"... {len(tags) - OUTLINE_LIMIT} more", ""])
    return Section(N_("Documents"), table=table)


def _load_list(s: Summary, save: SaveFile) -> bool:
    """117's version, mods and setup rows when the header holds a ``CorporationProfile``."""
    meta, header = save.part("meta.a7s"), save.part("header.a7s")
    top = header.tree().find("CorporationProfile") if header is not None and header.kind == FILEDB else None
    if top is None:
        return False
    summary117.version_rows(s, meta.tree() if meta is not None and meta.kind == FILEDB else None, top)
    summary117.mods_rows(s, top)
    summary117.setup_rows(s, top)
    return True


def summarize(save: SaveFile) -> Summary:
    s = Summary(save.game)
    if save.game == "anno1800" and _load_list(s, save):
        s.note(N_("Read with Anno 117's tag names; no Anno 1800 save was available to confirm them."))
    else:
        s.note(N_("No sample of this game's saves pinned its layout: only the documents' tags are listed, read only."))
    s.sections.append(outline(save))
    return s
